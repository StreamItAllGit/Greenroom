// @module GreenroomInstallerRunner.cs
// @purpose Wires Foundation AppEnvironment, InstallerRuntimeContext, 6-step path precedence, Wizard UI + Automation, and CLI.
// @inputs CLI arguments, version, entry Assembly, and optional InstallerRuntimeContext (@depends-on Foundation.Core, Foundation.Installer, Foundation.Automation)
// @outputs Process exit code (0 success, 1 failure, 2 disallowed Release automation/override, 3 sandbox containment) (@exports GreenroomInstallerRunner)

using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using Foundation.Core;
using Foundation.Installer;
#if DEBUG || GREENROOM_TEST_AUTOMATION
using Foundation.Automation;
#endif

namespace Greenroom.Installer;

internal static class GreenroomInstallerRunner
{
    public static int Run(string[] args, string version, Assembly entryAssembly, InstallerRuntimeContext? runtimeCtxOverride = null)
    {
        bool hasAutomationPort = Program.HasOption(args, "--automation-port");
#if !DEBUG && !GREENROOM_TEST_AUTOMATION
        if (hasAutomationPort)
        {
            Console.Error.WriteLine("[ERROR] --automation-port is disabled in Release builds (fail-closed ExitCode=2).");
            return 2;
        }
        if (Program.HasOption(args, "--manifest", "--payload-url", "--payload-sha256"))
        {
            Console.Error.WriteLine("[ERROR] Manifest/URL/hash overrides are disabled in Release builds (fail-closed ExitCode=2).");
            return 2;
        }
#endif

        InstallerRuntimeContext? runtimeCtx = runtimeCtxOverride;
        string? activeLogDir = null;
        string? activeAppHome = null;

        try
        {
            string? cliInstallDir = Program.GetOption(args, "--install-dir", "--target-dir");
            string? cliSuiteDir = Program.GetOption(args, "--suite-dir");
            string? cliProfile = Program.GetOption(args, "--profile");
            string? cliDataDir = Program.GetOption(args, "--data-dir");
            string? cliConfigPath = Program.GetOption(args, "--config");
            string? cliLogDir = Program.GetOption(args, "--log-dir");
            string? cliPayloadPath = Program.GetOption(args, "--payload");
            string? cliOfflineBundle = Program.GetOption(args, "--offline-bundle");
            string? cliManifestPath = Program.GetOption(args, "--manifest");
            string? cliPayloadUrl = Program.GetOption(args, "--payload-url");
            string? cliPayloadSha = Program.GetOption(args, "--payload-sha256");

            var appEnv = new AppEnvironment("greenroom", args, AppContext.BaseDirectory, "install-config.json");
            runtimeCtx ??= new InstallerRuntimeContext(cliDataDir, cliConfigPath, cliLogDir, dryRun: appEnv.DryRun, envPrefix: "GREENROOM");
            activeAppHome = Program.ResolveDirectoryOption(null, "APP_HOME");
            activeLogDir = Program.ResolveActiveLogDirectory(runtimeCtx, null, activeAppHome);

            bool testMode = runtimeCtx.IsIsolatedTestProcess;
            bool dryRun = runtimeCtx.DryRun || Program.HasFlag(args, "--dry-run") || Program.TruthyEnv("GREENROOM_DRY_RUN") || Program.TruthyEnv("DRY_RUN");
            bool silent = Program.HasFlag(args, "--silent", "--quiet", "/S", "--no-pause", "--unattended") || appEnv.TrayOnly || testMode;
            bool verifyOnly = Program.HasFlag(args, "--verify");
            bool uninstall = Program.HasFlag(args, "--uninstall") ||
                string.Equals(Path.GetFileName(Environment.ProcessPath), Program.UninstallerExeName, StringComparison.OrdinalIgnoreCase);
            bool purgeData = Program.HasFlag(args, "--purge-data");
            bool noShortcuts = Program.HasFlag(args, "--no-shortcuts");
            bool skipVenvFlag = Program.HasFlag(args, "--skip-venv");
            bool withVenvFlag = Program.HasFlag(args, "--with-venv");
            bool forcePipFlag = Program.HasFlag(args, "--force-pip");
            bool autostartFlag = Program.HasFlag(args, "--autostart");
            bool noDaemonRestart = Program.HasFlag(args, "--no-daemon-restart");
            bool migrateLegacyFlag = Program.HasFlag(args, "--migrate-legacy");

            InstallerConfigFile? fileCfg = Program.LoadOptionalConfig(runtimeCtx.ConfigFilePath, runtimeCtx, activeAppHome);
            string? requestedInstallDir = cliInstallDir ?? fileCfg?.InstallDir;
            string? requestedSuiteDir = cliSuiteDir ?? fileCfg?.SuiteDir;
            string? requestedOfflineBundle = cliOfflineBundle ?? fileCfg?.OfflineBundle;
            string? requestedManifest = cliManifestPath ?? fileCfg?.ManifestPath;
            string? requestedPayloadUrl = cliPayloadUrl ?? fileCfg?.PayloadUrl;
            string? requestedPayloadSha = cliPayloadSha ?? fileCfg?.PayloadSha256;
            string effectiveProfile = (cliProfile ?? fileCfg?.ProfileId ?? "full").Trim().ToLowerInvariant();
            bool createShortcuts = !noShortcuts && (fileCfg?.CreateShortcuts ?? true);
            bool enableAutostart = autostartFlag || (fileCfg?.Autostart ?? false);
            bool migrateLegacy = migrateLegacyFlag || (fileCfg?.MigrateLegacy ?? false);
            bool skipVenv = skipVenvFlag || (fileCfg?.SkipVenv ?? false) || (testMode && !withVenvFlag) || effectiveProfile == "lightweight";

            if (verifyOnly)
            {
                var verify = Program.VerifyPayload(cliPayloadPath, requestedOfflineBundle, requestedManifest, requestedPayloadUrl, requestedPayloadSha, runtimeCtx);
                Program.WriteLog(activeLogDir, verify.Valid ? "INFO" : "ERROR", verify.Message);
                Console.WriteLine(verify.Message);
                return verify.Valid ? 0 : 1;
            }

            if (runtimeCtx.IsSandboxActive || runtimeCtx.IsStage1Isolated)
            {
                _ = Program.ValidateAndNormalizeInstallDir(requestedInstallDir, runtimeCtx, activeAppHome, requestedSuiteDir);
                if (!string.IsNullOrWhiteSpace(requestedSuiteDir))
                    _ = Program.NormalizeAndGuardTargetPath(requestedSuiteDir, runtimeCtx, activeAppHome);
            }

            bool openGui = !silent && !dryRun && (string.IsNullOrWhiteSpace(requestedInstallDir) || hasAutomationPort) && OperatingSystem.IsWindows();
            if (openGui)
            {
                return RunGuiWizard(
                    args, version, entryAssembly, appEnv, runtimeCtx, requestedInstallDir, requestedSuiteDir,
                    effectiveProfile, createShortcuts, enableAutostart, migrateLegacy, uninstall, purgeData,
                    skipVenvFlag, withVenvFlag, forcePipFlag, noDaemonRestart, testMode, dryRun,
                    cliPayloadPath, requestedOfflineBundle, requestedManifest, requestedPayloadUrl, requestedPayloadSha,
                    activeAppHome, activeLogDir);
            }

            if (!silent) Program.PrintBanner(version);

            if (uninstall)
            {
                return Program.RunUninstall(
                    version, requestedInstallDir, purgeData, dryRun,
                    runtimeCtx.DataDirectory, activeAppHome, activeLogDir, null, runtimeCtx);
            }

            return Program.RunInstall(
                version, requestedInstallDir, cliPayloadPath, createShortcuts, skipVenv, forcePipFlag,
                enableAutostart, noDaemonRestart, dryRun, runtimeCtx.DataDirectory, activeAppHome, activeLogDir,
                null, requestedSuiteDir, effectiveProfile, migrateLegacy, runtimeCtx,
                requestedOfflineBundle, requestedManifest, requestedPayloadUrl, requestedPayloadSha);
        }
        catch (Exception ex) when (SandboxEnvironmentSeam.IsContainmentViolation(ex))
        {
            Program.WriteLog(activeLogDir, "ERROR", $"Sandbox containment violation (ExitCode=3): {ex.Message}");
            Console.Error.WriteLine($"[ERROR] {ex.Message}");
            return SandboxEnvironmentSeam.ContainmentExitCode;
        }
        catch (Exception ex)
        {
            Program.WriteLog(activeLogDir, "ERROR", $"Installer fatal error: {ex.Message}");
            Console.Error.WriteLine($"[ERROR] Greenroom Setup failed: {ex.Message}");
            return 1;
        }
    }

    private static int RunGuiWizard(
        string[] args, string version, Assembly entryAssembly, AppEnvironment appEnv, InstallerRuntimeContext runtimeCtx,
        string? requestedInstallDir, string? requestedSuiteDir, string effectiveProfile,
        bool createShortcuts, bool enableAutostart, bool migrateLegacy, bool uninstall, bool purgeData,
        bool skipVenvFlag, bool withVenvFlag, bool forcePipFlag, bool noDaemonRestart, bool testMode, bool dryRun,
        string? cliPayloadPath, string? offlineBundlePath, string? manifestPath, string? payloadUrl, string? payloadSha,
        string? activeAppHome, string? preInstallLogDir)
    {
        InstallTargetResolution initialTarget = Program.ResolveInstallTarget(runtimeCtx, requestedInstallDir, requestedSuiteDir, null, activeAppHome);
        string standaloneDir = Program.ResolveStandaloneFallbackDir(runtimeCtx);
        string defaultMotherDir = initialTarget.MotherSuiteDir
            ?? Program.TryDetectMotherSuiteDirectory(runtimeCtx, requestedSuiteDir)
            ?? Program.GetDefaultMotherSuiteDirectory(runtimeCtx);

        DualInstallerManifestDto? dualManifest = Program.LoadDualInstallerManifest(entryAssembly, manifestPath, runtimeCtx);
        bool isOffline = Program.IsOfflineInstaller(entryAssembly, dualManifest) || !string.IsNullOrWhiteSpace(offlineBundlePath);
        InstallerThemeOptions theme = BuildThemeOptions(initialTarget.InstallDir, defaultMotherDir, version, entryAssembly, initialTarget.UmbrellaSubpath);
        var coreEngine = new InstallerEngineCore(theme, appEnv, runtimeCtx.Registry);

        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        InstallerWizardForm? wizard = null;
        CheckBox? chkFullSuite = null;
        wizard = new InstallerWizardForm(
            coreEngine,
            isUninstallMode: uninstall,
            initialInstallDir: initialTarget.InstallDir,
            customInstallAction: (ct, progress) => Task.Run(() =>
            {
                string chosenDir = wizard?.InstallDirTextBox.Text.Trim() ?? initialTarget.InstallDir;
                string? chosenSuite = wizard?.SecondaryInstallDir is { Length: > 0 } s ? s : requestedSuiteDir;
                bool fullSuiteChecked = chkFullSuite?.Checked ?? initialTarget.IsMotherSuiteMode;
                string chosenProfile = wizard?.SelectedProfileId ?? effectiveProfile;
                bool chosenMigrate = wizard?.MigrateLegacyChecked ?? migrateLegacy;
                bool chosenShortcuts = wizard?.ShortcutsCheckBox.Checked ?? createShortcuts;
                bool chosenAutoStart = wizard?.AutoStartCheckBox.Checked ?? enableAutostart;
                bool chosenSkipVenv = skipVenvFlag || (testMode && !withVenvFlag) || chosenProfile == "lightweight";
                string guiLogDir = Program.ResolveActiveLogDirectory(runtimeCtx, chosenDir, activeAppHome);

                int code = uninstall
                    ? Program.RunUninstall(version, chosenDir, purgeData, dryRun, runtimeCtx.DataDirectory, activeAppHome, guiLogDir, null, runtimeCtx, progress)
                    : Program.RunInstall(
                        version, chosenDir, cliPayloadPath, chosenShortcuts, chosenSkipVenv, forcePipFlag,
                        chosenAutoStart, noDaemonRestart, dryRun, runtimeCtx.DataDirectory, activeAppHome, guiLogDir,
                        null, chosenSuite, chosenProfile, chosenMigrate, runtimeCtx,
                        offlineBundlePath, manifestPath, payloadUrl, payloadSha, fullSuiteChecked, progress);

                return new FoundationInstallResult(
                    code == 0, chosenDir, string.Empty, 0,
                    code == 0 ? "Operation completed." : "Operation failed; inspect installer.log for details.",
                    Program.HasExistingInstallationArtifacts(chosenDir));
            }, ct),
            initialProfileId: effectiveProfile,
            initialSecondaryDir: defaultMotherDir,
            initialMigrateLegacy: migrateLegacy);

        chkFullSuite = EnhanceWizardControls(wizard, isOffline, uninstall, initialTarget.IsMotherSuiteMode, standaloneDir, defaultMotherDir);
        Program.WriteLog(preInstallLogDir, "INFO", $"Initialized GUI Wizard v{version} (Delivery={(isOffline ? "offlineSetup" : "onlineSetup")}, DefaultDir='{initialTarget.InstallDir}').");

#if DEBUG || GREENROOM_TEST_AUTOMATION
        string? snapRoot = Program.GetOption(args, "--snapshot-dir")
            ?? Environment.GetEnvironmentVariable("GREENROOM_AUTOMATION_SNAPSHOT_ROOT")
            ?? (runtimeCtx.IsSandboxActive ? runtimeCtx.Sandbox.SandboxRoot : null);
        using AutomationLoopbackServer? autoServer = AutomationCliHelper.TryCreateFromArgs(
            "greenroom-installer", NormalizeAutomationArgs(args), wizard, allowedSnapshotRoot: snapRoot);
        if (autoServer is not null)
            wizard.Shown += (_, _) => autoServer.Start();
#else
        _ = args;
#endif
        Application.Run(wizard);
        return wizard.ExitCode;
    }

    private static CheckBox? EnhanceWizardControls(
        InstallerWizardForm wizard, bool isOffline, bool isUninstall,
        bool initialSuiteMode, string standaloneDir, string defaultMotherDir)
    {
        wizard.HeaderPanel.Name = "headerPanel";
        wizard.TitleLabel.Name = "lblTitle";
        wizard.SubtitleLabel.Name = "lblSub";
        wizard.ModeBadgeLabel.Name = "lblModeBadge";
        wizard.InstallDirTextBox.Name = "txtInstallDir";
        wizard.BrowseButton.Name = "btnBrowse";
        wizard.SecondaryDirTextBox.Name = "txtSecondaryDir";
        wizard.OptionsPanel.Name = "optionsPanel";
        wizard.LightweightProfileRadioButton.Name = "rbProfileLightweight";
        wizard.FullProfileRadioButton.Name = "rbProfileFull";
        wizard.CustomProfileRadioButton.Name = "rbProfileCustom";
        wizard.ShortcutsCheckBox.Name = "chkShortcuts";
        wizard.AutoStartCheckBox.Name = "chkAutoStart";
        wizard.LaunchAfterCheckBox.Name = "chkLaunchAfter";
        wizard.MigrateLegacyCheckBox.Name = "chkMigrateLegacy";
        wizard.StatusLabel.Name = "lblStatus";
        wizard.SetupProgressBar.Name = "progressBar";
        wizard.DetailsToggleButton.Name = "btnToggleDetails";
        wizard.LogTextBox.Name = "txtLog";
        wizard.PrimaryButton.Name = "btnPrimary";
        wizard.CancelButton.Name = "btnCancel";

        var deliveryBadge = new Label
        {
            Name = "lblDeliveryBadge",
            Text = isOffline ? "OFFLINE SETUP" : "ONLINE SETUP",
            AutoSize = true,
            Font = new Font("Segoe UI Semibold", 7.75f, FontStyle.Bold),
            ForeColor = isOffline ? Color.FromArgb(16, 185, 129) : Color.FromArgb(56, 189, 248),
            BackColor = isOffline ? Color.FromArgb(6, 78, 59) : Color.FromArgb(12, 74, 110),
            Padding = new Padding(6, 2, 6, 2)
        };
        wizard.HeaderPanel.Controls.Add(deliveryBadge);
        void PositionBadge()
        {
            int hw = wizard.HeaderPanel.ClientSize.Width > 0 ? wizard.HeaderPanel.ClientSize.Width : InstallerWizardForm.DefaultClientWidth;
            deliveryBadge.Location = new Point(Math.Max(22, hw - 22 - deliveryBadge.PreferredWidth), 41);
        }
        PositionBadge();
        wizard.HeaderPanel.Resize += (_, _) => PositionBadge();

        if (isUninstall) return null;

        int toggleY = wizard.MigrateLegacyCheckBox.Visible
            ? wizard.MigrateLegacyCheckBox.Bottom + 4
            : wizard.LaunchAfterCheckBox.Bottom + 4;
        var chkFullSuite = new CheckBox
        {
            Name = "chkFullSuiteMother",
            Text = "Install inside Mother Suite (Full Suite)",
            Checked = initialSuiteMode,
            AutoSize = true,
            ForeColor = InstallerWizardControls.TextPrimary,
            BackColor = Color.Transparent,
            Font = new Font("Segoe UI", 9.0f, FontStyle.Regular),
            Location = new Point(16, toggleY),
            Cursor = Cursors.Hand
        };
        wizard.OptionsPanel.Controls.Add(chkFullSuite);
        wizard.OptionsPanel.Height = Math.Max(wizard.OptionsPanel.Height, toggleY + 32);

        void SyncSuiteTarget()
        {
            bool suiteOn = chkFullSuite.Checked;
            wizard.SecondaryDirTextBox.Enabled = suiteOn;
            if (suiteOn)
            {
                string suiteRoot = string.IsNullOrWhiteSpace(wizard.SecondaryDirTextBox.Text) ? defaultMotherDir : wizard.SecondaryDirTextBox.Text.Trim();
                wizard.SecondaryDirTextBox.Text = suiteRoot;
                wizard.InstallDirTextBox.Text = Path.Combine(suiteRoot, "Greenroom");
            }
            else if (Path.GetFileName(wizard.InstallDirTextBox.Text.Trim()).Equals("Greenroom", StringComparison.OrdinalIgnoreCase))
            {
                wizard.InstallDirTextBox.Text = standaloneDir;
            }
        }
        chkFullSuite.CheckedChanged += (_, _) => SyncSuiteTarget();
        wizard.SecondaryDirTextBox.TextChanged += (_, _) =>
        {
            if (chkFullSuite.Checked && !string.IsNullOrWhiteSpace(wizard.SecondaryDirTextBox.Text))
                wizard.InstallDirTextBox.Text = Path.Combine(wizard.SecondaryDirTextBox.Text.Trim(), "Greenroom");
        };
        wizard.SecondaryDirTextBox.Enabled = initialSuiteMode;
        wizard.UpdateLayoutBounds();
        return chkFullSuite;
    }

#if DEBUG || GREENROOM_TEST_AUTOMATION
    private static string[] NormalizeAutomationArgs(string[] args)
    {
        var normalized = new List<string>(args.Length + 2);
        foreach (string a in args)
        {
            if (a.StartsWith("--automation-port=", StringComparison.OrdinalIgnoreCase))
            { normalized.Add("--automation-port"); normalized.Add(a["--automation-port=".Length..]); }
            else if (a.StartsWith("--automation-token=", StringComparison.OrdinalIgnoreCase))
            { normalized.Add("--automation-token"); normalized.Add(a["--automation-token=".Length..]); }
            else normalized.Add(a);
        }
        return normalized.ToArray();
    }
#endif

    internal static InstallerThemeOptions BuildThemeOptions(
        string defaultInstallDir, string defaultSuiteDir, string version,
        Assembly? entryAssembly = null, string? umbrellaSubpath = null)
    {
        InstallerMarkupManifest? markup = InstallerManifestStore.TryLoadFromDirectoryOrAssembly(AppContext.BaseDirectory, entryAssembly);
        InstallerThemeOptions baseTheme = markup is not null
            ? InstallerManifestStore.ToThemeOptions(markup, defaultInstallDir)
            : new InstallerThemeOptions { AppId = "greenroom", DisplayName = "Greenroom", Version = version, DefaultInstallDir = defaultInstallDir };

        return baseTheme with
        {
            Version = version,
            DefaultInstallDir = defaultInstallDir,
            BrandAccentHex = "#10B981",
            ExecutableFileName = "Open Greenroom.bat",
            UninstallerFileName = Program.UninstallerExeName,
            BootstrapSharedSuiteDaemon = false,
            AllowAutoStart = false,
            StudioName = "StreamItAll",
            SuiteName = "Mother",
            UmbrellaSubpath = umbrellaSubpath ?? Program.StandaloneUmbrellaSubpath,
            AvailableProfiles =
            [
                new InstallerProfileOption("lightweight", InstallerProfileKind.Lightweight, "Lightweight", "Stage Clean Release Root without local .venv creation."),
                new InstallerProfileOption("full", InstallerProfileKind.Full, "Full Suite App", "Complete Clean Release Root with Python .venv bootstrap.", IsDefault: true)
            ],
            DefaultProfileId = "full",
            SecondaryPathPicker = new SecondaryPathPickerOptions(
                FlagName: "--suite-dir",
                Label: "Mother Suite Directory (Optional):",
                DefaultPath: defaultSuiteDir,
                Placeholder: defaultSuiteDir,
                Tooltip: "Root umbrella folder where Mother orchestrator resides (StreamItAll\\Mother)."),
            AllowLegacyMigration = true
        };
    }
}
