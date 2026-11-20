// @module InstallerEngine.PythonBootstrap.cs
// @purpose Python virtual environment creation, live streaming pip wheelhouse install, strict --no-index offline mode, and DB init/migrate.
// @inputs Target install directory, offline mode flag, version, and progress callback (@depends-on InstallerEngine.Verification.cs)
// @outputs Bootstrapped app\.venv, migrated YAML configs, and initialized SQLite database (@exports Program.BootstrapPythonEnvironment, Program.BuildPipRequirementsInstallArgs)

using System.Text;

namespace Greenroom.Installer;

public static partial class Program
{
    internal static IReadOnlyList<string> BuildPipRequirementsInstallArgs(
        string wheelhouse, string reqPath, bool isOfflineMode, out bool usesNoIndex)
    {
        bool hasWheels = Directory.Exists(wheelhouse) && Directory.GetFiles(wheelhouse, "*.whl").Length > 0;
        if (isOfflineMode)
        {
            if (!File.Exists(reqPath) || !hasWheels)
            {
                throw new InvalidOperationException(FormatOfflinePipFailure3PartError(
                    DefaultFallbackVersion,
                    !File.Exists(reqPath) ? $"Missing requirements.txt at '{reqPath}'." : $"Bundled wheelhouse '{wheelhouse}' contains no .whl packages."));
            }
            usesNoIndex = true;
            return ["install", "--disable-pip-version-check", "--no-index", "--find-links", wheelhouse, "-r", reqPath];
        }
        if (hasWheels)
        {
            usesNoIndex = true;
            return ["install", "--disable-pip-version-check", "--no-index", "--find-links", wheelhouse, "-r", reqPath];
        }
        usesNoIndex = false;
        return ["install", "--disable-pip-version-check", "-r", reqPath];
    }

    internal static string FormatOfflinePipFailure3PartError(string version, string rawOutput)
    {
        string detailLine = rawOutput.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries)
            .FirstOrDefault(l =>
                l.Contains("No matching distribution", StringComparison.OrdinalIgnoreCase) ||
                l.Contains("not a supported wheel", StringComparison.OrdinalIgnoreCase) ||
                l.Contains("Could not find a version", StringComparison.OrdinalIgnoreCase) ||
                l.Contains("Requires-Python", StringComparison.OrdinalIgnoreCase) ||
                l.Contains("ERROR:", StringComparison.OrdinalIgnoreCase))
            ?? rawOutput.Trim();
        if (detailLine.Length > 280) detailLine = detailLine[..280] + "...";
        return $"What failed: Offline Python dependency installation failed (--no-index strict mode, zero PyPI fallback). " +
               $"Why: {detailLine} " +
               $"Next step: Ensure Python 3.11+ (win_amd64) is installed and use a complete 'Greenroom-Offline-Setup-v{version}-win-x64.exe' matching requirements.txt.";
    }

    internal static void BootstrapPythonEnvironment(
        string installDir, string? logDir, bool forcePip = false, bool isOfflineMode = false,
        string version = DefaultFallbackVersion, Action<int, string>? onProgress = null, string? activeDataDir = null)
    {
        string appRoot = Directory.Exists(Path.Combine(installDir, "app")) ? Path.Combine(installDir, "app") : installDir;
        string venvDir = File.Exists(Path.Combine(installDir, ".venv", "Scripts", "python.exe")) ? Path.Combine(installDir, ".venv") : Path.Combine(appRoot, ".venv");
        string venvPy = Path.Combine(venvDir, "Scripts", "python.exe");
        string venvPip = Path.Combine(venvDir, "Scripts", "pip.exe");
        string wheelhouse = Directory.Exists(Path.Combine(appRoot, "wheelhouse")) ? Path.Combine(appRoot, "wheelhouse") : Path.Combine(installDir, "wheelhouse");

        onProgress?.Invoke(62, "Locating Python 3.11+ runtime...");
        if (!File.Exists(venvPy))
        {
            string? basePython = FindSystemPython();
            if (string.IsNullOrWhiteSpace(basePython))
            {
                WriteLog(logDir, "WARN", "Python 3.11+ was not detected on PATH; skipped .venv creation.");
                return;
            }
            onProgress?.Invoke(65, "Creating Python virtual environment (.venv)...");
            WriteLog(logDir, "INFO", $"Creating Python virtual environment in '{venvDir}' using '{basePython}'...");
            RunProcessOrThrow(basePython, ["-m", "venv", venvDir], installDir, 180_000, (ln, err) => WriteLog(logDir, err ? "WARN" : "INFO", $"[venv] {ln}"));
        }

        string reqPath = File.Exists(Path.Combine(appRoot, "requirements.txt")) ? Path.Combine(appRoot, "requirements.txt") : Path.Combine(installDir, "requirements.txt");
        string reqStampPath = Path.Combine(venvDir, ".requirements.sha256");
        string reqHash = ComputeFileSha256Hex(reqPath);
        string prevReqHash = File.Exists(reqStampPath) ? File.ReadAllText(reqStampPath, Encoding.UTF8).Trim() : string.Empty;

        if (forcePip || string.IsNullOrWhiteSpace(prevReqHash) || !string.Equals(reqHash, prevReqHash, StringComparison.OrdinalIgnoreCase))
        {
            if (File.Exists(reqPath))
            {
                IReadOnlyList<string> pipArgs = BuildPipRequirementsInstallArgs(wheelhouse, reqPath, isOfflineMode, out bool usesNoIndex);
                onProgress?.Invoke(70, usesNoIndex ? "Installing Python dependencies offline from wheelhouse..." : "Installing Python dependencies...");
                WriteLog(logDir, "INFO", $"Running pip ({(isOfflineMode ? "strict --no-index" : "wheelhouse/online")}): {string.Join(' ', pipArgs)}");

                int pipPct = 70;
                long lastUiTick = 0;
                Action<string, bool> pipLineHandler = (line, isErr) =>
                {
                    if (string.IsNullOrWhiteSpace(line)) return;
                    WriteLog(logDir, isErr ? "WARN" : "INFO", $"[pip] {line}");
                    bool keyLine = line.StartsWith("Collecting ", StringComparison.OrdinalIgnoreCase) ||
                                   line.StartsWith("Processing ", StringComparison.OrdinalIgnoreCase) ||
                                   line.StartsWith("Installing collected", StringComparison.OrdinalIgnoreCase) ||
                                   line.StartsWith("Successfully installed", StringComparison.OrdinalIgnoreCase);
                    long nowTick = Environment.TickCount64;
                    if (keyLine || nowTick - lastUiTick >= 350)
                    {
                        lastUiTick = nowTick;
                        if (keyLine && pipPct < 82) pipPct++;
                        string trimmed = line.Length > 78 ? line[..78] + "..." : line;
                        onProgress?.Invoke(pipPct, $"pip: {trimmed}");
                    }
                };

                var pipRes = RunProcess(venvPip, pipArgs, installDir, 300_000, pipLineHandler);
                if (pipRes.TimedOut)
                    throw new TimeoutException("pip install timed out after 300s and its process tree was terminated.");
                if (pipRes.ExitCode != 0)
                {
                    if (isOfflineMode)
                        throw new InvalidOperationException(FormatOfflinePipFailure3PartError(version, string.IsNullOrWhiteSpace(pipRes.StdErr) ? pipRes.StdOut : pipRes.StdErr));
                    if (usesNoIndex)
                    {
                        WriteLog(logDir, "WARN", "Offline wheelhouse incomplete in Online Setup mode; falling back to PyPI...");
                        RunProcessOrThrow(venvPip, ["install", "--disable-pip-version-check", "--find-links", wheelhouse, "-r", reqPath], installDir, 300_000, pipLineHandler);
                    }
                    else
                    {
                        throw new InvalidOperationException($"pip install failed (exit {pipRes.ExitCode}): {pipRes.StdErr}".Trim());
                    }
                }
            }
            if (!string.IsNullOrWhiteSpace(reqHash)) File.WriteAllText(reqStampPath, reqHash, Encoding.UTF8);
        }

        string pyprojPath = File.Exists(Path.Combine(appRoot, "pyproject.toml")) ? Path.Combine(appRoot, "pyproject.toml") : Path.Combine(installDir, "pyproject.toml");
        string editableTarget = File.Exists(Path.Combine(appRoot, "pyproject.toml")) ? appRoot : installDir;
        string pyprojStampPath = Path.Combine(venvDir, ".pyproject.sha256");
        string pyprojHash = ComputeFileSha256Hex(pyprojPath);
        string prevPyprojHash = File.Exists(pyprojStampPath) ? File.ReadAllText(pyprojStampPath, Encoding.UTF8).Trim() : string.Empty;
        string eggLink = Path.Combine(venvDir, "Lib", "site-packages", "greenroom.egg-link");

        if (forcePip || !File.Exists(eggLink) || string.IsNullOrWhiteSpace(prevPyprojHash) || !string.Equals(pyprojHash, prevPyprojHash, StringComparison.OrdinalIgnoreCase))
        {
            onProgress?.Invoke(84, "Registering greenroom editable package...");
            RunProcessOrThrow(venvPip, ["install", "--disable-pip-version-check", "--no-index", "--no-build-isolation", "--no-deps", "-e", editableTarget], installDir, 180_000, (ln, err) => WriteLog(logDir, err ? "WARN" : "INFO", $"[pip-e] {ln}"));
            if (!string.IsNullOrWhiteSpace(pyprojHash)) File.WriteAllText(pyprojStampPath, pyprojHash, Encoding.UTF8);
        }

        onProgress?.Invoke(87, "Merging configuration templates...");
        TryMigrateConfigsWithAvailablePython(installDir, logDir);

        var cliEnv = new Dictionary<string, string?>
        {
            ["GREENROOM_HOME"] = installDir,
            ["APP_HOME"] = null,
            ["DATA_DIR"] = string.IsNullOrWhiteSpace(activeDataDir) ? null : activeDataDir,
            ["GREENROOM_DATA_DIR"] = string.IsNullOrWhiteSpace(activeDataDir) ? null : activeDataDir
        };
        onProgress?.Invoke(89, "Initializing Greenroom database...");
        var migRes = RunProcess(venvPy, ["-m", "greenroom.cli", "init-db", "--no-pause"], installDir, 120_000, (ln, err) => WriteLog(logDir, err ? "WARN" : "INFO", $"[init-db] {ln}"), cliEnv);
        if (migRes.ExitCode == 0)
        {
            onProgress?.Invoke(91, "Applying database migrations...");
            RunProcess(venvPy, ["-m", "greenroom.cli", "migrate", "--no-pause"], installDir, 120_000, (ln, err) => WriteLog(logDir, err ? "WARN" : "INFO", $"[migrate] {ln}"), cliEnv);
        }
    }

    private static string? ResolveInstalledOrSystemPython(string installDir)
    {
        string appVenvPy = Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe");
        if (File.Exists(appVenvPy)) return appVenvPy;
        string rootVenvPy = Path.Combine(installDir, ".venv", "Scripts", "python.exe");
        if (File.Exists(rootVenvPy)) return rootVenvPy;
        string? envPy = Environment.GetEnvironmentVariable("GREENROOM_PYTHON_EXE")?.Trim();
        return !string.IsNullOrWhiteSpace(envPy) && File.Exists(envPy) ? envPy : FindSystemPython();
    }

    private static void TryMigrateConfigsWithAvailablePython(string installDir, string? logDir)
    {
        string? pyExe = ResolveInstalledOrSystemPython(installDir);
        if (string.IsNullOrWhiteSpace(pyExe)) return;
        try
        {
            string pkgRoot = Directory.Exists(Path.Combine(installDir, "app", "greenroom")) ? Path.Combine(installDir, "app") : installDir;
            string cfgDir = Path.Combine(installDir, "config");
            string script =
                "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); " +
                "from greenroom.core.config_migrator import migrate_yaml_file; c = Path(sys.argv[2]); " +
                "migrate_yaml_file(c / 'greenroom.example.yaml', c / 'greenroom.yaml'); " +
                "migrate_yaml_file(c / 'schedule.example.yaml', c / 'schedule.yaml')";
            var res = RunProcess(pyExe, ["-c", script, pkgRoot, cfgDir], installDir, 60_000);
            if (res.ExitCode == 0)
                WriteLog(logDir, "INFO", "Merged template configuration keys into existing YAML configs.");
        }
        catch { /* Non-fatal if system python lacks PyYAML */ }
    }

    private static string? FindSystemPython()
    {
        foreach (string cmd in new[] { "python.exe", "py.exe" })
        {
            try
            {
                string[] probeArgs = cmd.Equals("py.exe", StringComparison.OrdinalIgnoreCase)
                    ? ["-3", "-c", "import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)"]
                    : ["-c", "import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)"];
                var res = RunProcess(cmd, probeArgs, AppContext.BaseDirectory, 15_000);
                if (res.ExitCode == 0)
                {
                    string exe = res.StdOut.Trim().Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? string.Empty;
                    if (!string.IsNullOrWhiteSpace(exe) && File.Exists(exe)) return exe;
                }
            }
            catch { /* Continue probing */ }
        }
        return null;
    }
}
