// @module Greenroom.Installer.Tests.ProcessStreamingAndLogging
// @purpose Tests Release --automation-port ExitCode=2, async RunProcess deadlock freedom, GUI log containment, and --no-index offline fail-closed.
// @inputs Program.RunProcess, GreenroomInstallerRunner.Run, and BuildPipRequirementsInstallArgs (@depends-on Xunit, Foundation.Core)
// @outputs Verified Release automation rejection, non-blocking process streaming, sandbox logs, and offline pip error formatting (@exports ProcessStreamingAndLoggingTests)

using Foundation.Core;
using Foundation.Installer;
using Xunit;

namespace Greenroom.Installer.Tests;

public sealed class ProcessStreamingAndLoggingTests
{
    [Fact]
    public void ReleaseBuild_AutomationPortAndOverrides_FailClosedWithExitCode2()
    {
        if (!IsReleaseNonAutomationBuild())
        {
            return;
        }

        int runnerCode = GreenroomInstallerRunner.Run(["--automation-port", "19999"], "0.3.0", typeof(Program).Assembly);
        Assert.Equal(2, runnerCode);

        int mainCode = Program.Main(["--automation-port=19999"]);
        Assert.Equal(2, mainCode);

        int overrideCode = GreenroomInstallerRunner.Run(["--payload-url", "https://example.com/p.zip"], "0.3.0", typeof(Program).Assembly);
        Assert.Equal(2, overrideCode);
    }

    private static bool IsReleaseNonAutomationBuild()
    {
#if DEBUG || GREENROOM_TEST_AUTOMATION
        return false;
#else
        return true;
#endif
    }

    [Fact]
    public void RunProcess_LargeStdoutAndStderr_StreamsLiveWithoutDeadlock_And_KillsOnTimeout()
    {
        using var fx = new InstallerTestFixtureHelper();
        string scriptPath = Path.Combine(fx.TempRoot, "emit_large.ps1");
        File.WriteAllText(scriptPath, """
            1..220 | ForEach-Object {
                [Console]::Out.WriteLine(("OUT_" + $_ + "_" + ("A" * 240)))
                [Console]::Error.WriteLine(("ERR_" + $_ + "_" + ("B" * 240)))
            }
            """);

        int lineCount = 0;
        var res = Program.RunProcess(
            "powershell",
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", scriptPath],
            fx.TempRoot,
            timeoutMs: 30_000,
            onOutputLine: (_, _) => Interlocked.Increment(ref lineCount));

        Assert.Equal(0, res.ExitCode);
        Assert.False(res.TimedOut);
        Assert.True(lineCount >= 440, $"Expected >= 440 streamed lines, got {lineCount}");
        Assert.Contains("OUT_220_", res.StdOut);
        Assert.Contains("ERR_220_", res.StdErr);

        var timed = Program.RunProcess(
            "cmd.exe",
            ["/c", "ping 127.0.0.1 -n 10 > nul"],
            fx.TempRoot,
            timeoutMs: 250);
        Assert.True(timed.TimedOut);
        Assert.Equal(-1, timed.ExitCode);
    }

    [Fact]
    public void ResolveActiveLogDirectory_RespectsInstallDirAndSandboxContainment()
    {
        using var fx = new InstallerTestFixtureHelper();
        fx.SetEnv("APP_ISOLATED_TEST", null);
        (SandboxResolution sbx, InstallerRuntimeContext sbxCtx, _, _, string dDrive) = fx.CreateSandboxContext();

        string hostTemp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        string preInstallSbx = Program.ResolveActiveLogDirectory(sbxCtx);
        Assert.True(SandboxEnvironmentSeam.IsPathWithinSandbox(preInstallSbx, sbx));
        Assert.StartsWith(sbx.SystemDriveRoot!, preInstallSbx, StringComparison.OrdinalIgnoreCase);
        Assert.NotEqual(Path.Combine(hostTemp, "Greenroom-Setup-logs"), preInstallSbx, StringComparer.OrdinalIgnoreCase);

        string installDir = Path.Combine(dDrive, "Programs", "Greenroom");
        Assert.Equal(Path.Combine(installDir, "logs"), Program.ResolveActiveLogDirectory(sbxCtx, installDir));

        fx.SetEnv("APP_ISOLATED_TEST", "1");
        string isoData = Path.Combine(fx.TempRoot, "iso_data");
        var isoCtx = new InstallerRuntimeContext(cliDataDir: isoData, envPrefix: "GREENROOM");
        string isoLog = Program.ResolveActiveLogDirectory(isoCtx);
        Assert.Equal(Path.Combine(isoData, "logs"), isoLog);
    }

    [Fact]
    public void OfflineMode_EnforcesStrictNoIndex_And_ThreePartErrorFormatting()
    {
        using var fx = new InstallerTestFixtureHelper();
        string wheelhouse = Path.Combine(fx.TempRoot, "wheelhouse");
        string reqPath = Path.Combine(fx.TempRoot, "requirements.txt");
        Directory.CreateDirectory(wheelhouse);
        File.WriteAllText(reqPath, "pydantic>=2.6.0\n");

        // Empty wheelhouse fails closed immediately in offline mode
        InvalidOperationException missingWheelsEx = Assert.Throws<InvalidOperationException>(
            () => Program.BuildPipRequirementsInstallArgs(wheelhouse, reqPath, isOfflineMode: true, out _));
        Assert.Contains("--no-index strict mode", missingWheelsEx.Message);
        Assert.Contains("Greenroom-Offline-Setup-v", missingWheelsEx.Message);

        // Non-empty wheelhouse builds strict --no-index command without -q
        File.WriteAllText(Path.Combine(wheelhouse, "pydantic-2.9.0-py3-none-any.whl"), "stub");
        IReadOnlyList<string> args = Program.BuildPipRequirementsInstallArgs(wheelhouse, reqPath, isOfflineMode: true, out bool noIndex);
        Assert.True(noIndex);
        Assert.Contains("--no-index", args);
        Assert.DoesNotContain("-q", args);

        string err3 = Program.FormatOfflinePipFailure3PartError("0.3.0", "ERROR: No matching distribution found for missingpkg==1.0");
        Assert.Contains("What failed:", err3);
        Assert.Contains("Why:", err3);
        Assert.Contains("Next step:", err3);
        Assert.Contains("missingpkg==1.0", err3);

        // Deferred self-delete uses bounded retry loop for /L %i in (1,1,15)
        string selfDel = Program.BuildDeferredSelfDeleteCommand(
            Path.Combine(fx.TempRoot, "app", Program.UninstallerExeName),
            Path.Combine(fx.TempRoot, "app"));
        Assert.Contains("for /L %i in (1,1,15)", selfDel);
    }
}
