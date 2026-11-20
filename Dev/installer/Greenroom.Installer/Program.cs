// @module Program.cs
// @purpose Single-EXE setup/uninstaller entrypoint loading GZip Foundation assemblies via AssemblyLoadContext.Default.LoadFromStream.
// @inputs CLI arguments, embedded installer.manifest.json / payload resources, and Foundation.*.dll.gz
// @outputs Process exit code (0 on success, 2 on disallowed Release automation, 3 on sandbox containment) (@exports Program)

using System.Collections.Concurrent;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;

namespace Greenroom.Installer;

public static partial class Program
{
    internal const string DefaultFallbackVersion = "0.3.0";
    private const int AttachParentProcess = -1;
    internal const string EmbeddedPayloadZipResource = "Greenroom.Installer.Embedded.payload.zip";
    internal const string EmbeddedPayloadMetaResource = "Greenroom.Installer.Embedded.payload-meta.json";
    internal const string EmbeddedInstallerManifestResource = "Greenroom.Installer.Embedded.installer.manifest.json";
    internal const string InstallManifestFileName = "install-manifest.json";
    internal const string UninstallerExeName = "Greenroom-Uninstall.exe";
    internal const string WindowsUninstallRegistrySubKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Greenroom";
    internal const string StreamItAllGreenroomRegistrySubKey = @"Software\StreamItAll\Greenroom";
    internal const string MotherAppRegistrationSubKey = @"Software\StreamItAll\Mother\Apps\greenroom";
    internal const string MotherInstallRegistrySubKey = @"Software\StreamItAll\Mother";

    private static readonly ConcurrentDictionary<string, Assembly> LoadedFoundationAssemblies =
        new(StringComparer.OrdinalIgnoreCase);

    static Program()
    {
        AssemblyLoadContext.Default.Resolving += (_, name) => LoadEmbeddedFoundationAssembly(name.Name);
        AppDomain.CurrentDomain.AssemblyResolve += (_, e) => LoadEmbeddedFoundationAssembly(new AssemblyName(e.Name).Name);
    }

    private static Assembly? LoadEmbeddedFoundationAssembly(string? asmName)
    {
        bool isAllowed = asmName is "Foundation.Core" or "Foundation.Installer";
#if DEBUG || GREENROOM_TEST_AUTOMATION
        isAllowed = isAllowed || asmName is "Foundation.Automation" or "Foundation.UI";
#endif
        if (!isAllowed || string.IsNullOrWhiteSpace(asmName))
        {
            return null;
        }

        return LoadedFoundationAssemblies.GetOrAdd(asmName, static key =>
        {
            using Stream? gzStream = typeof(Program).Assembly.GetManifestResourceStream(key + ".dll.gz")
                ?? throw new FileNotFoundException($"Missing embedded Foundation assembly resource '{key}.dll.gz'.");
            using var decompressor = new GZipStream(gzStream, CompressionMode.Decompress);
            using var ms = new MemoryStream();
            decompressor.CopyTo(ms);
            ms.Position = 0;
            return AssemblyLoadContext.Default.LoadFromStream(ms);
        });
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int dwProcessId);

    internal static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true
    };

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length > 0 && OperatingSystem.IsWindows())
        {
            try
            {
                if (!Console.IsOutputRedirected && !Console.IsErrorRedirected)
                {
                    AttachConsole(AttachParentProcess);
                }
            }
            catch { /* Ignore if redirected or no parent console */ }
        }

        try { Console.OutputEncoding = Encoding.UTF8; } catch { /* Ignore without console */ }

#if !DEBUG && !GREENROOM_TEST_AUTOMATION
        if (HasOption(args, "--automation-port"))
        {
            Console.Error.WriteLine("[ERROR] --automation-port is disabled in Release builds (fail-closed ExitCode=2).");
            return 2;
        }
#endif

        string version = ResolveProductVersion();
        if (HasFlag(args, "--version", "-v"))
        {
            Console.WriteLine(version);
            return 0;
        }

        if (HasFlag(args, "--help", "-h", "/?"))
        {
            PrintBanner(version);
            PrintUsage();
            return 0;
        }

        return RunHost(args, version);
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int RunHost(string[] args, string version) =>
        GreenroomInstallerRunner.Run(args, version, typeof(Program).Assembly);

    public static bool HasExistingInstallationArtifacts(string? installDir)
    {
        if (string.IsNullOrWhiteSpace(installDir)) return false;
        try
        {
            string full = Path.GetFullPath(installDir);
            if (!Directory.Exists(full)) return false;
            return File.Exists(Path.Combine(full, InstallManifestFileName)) ||
                   File.Exists(Path.Combine(full, "app", InstallManifestFileName)) ||
                   File.Exists(Path.Combine(full, "Open Greenroom.bat")) ||
                   File.Exists(Path.Combine(full, "data", "greenroom.db")) ||
                   File.Exists(Path.Combine(full, "app", "pyproject.toml")) ||
                   File.Exists(Path.Combine(full, "pyproject.toml"));
        }
        catch { return false; }
    }

    internal static bool HasFlag(string[] args, params string[] names) =>
        args.Any(a => names.Any(n => string.Equals(a, n, StringComparison.OrdinalIgnoreCase)));

    internal static bool HasOption(string[] args, params string[] names) =>
        args.Any(a => names.Any(n =>
            string.Equals(a, n, StringComparison.OrdinalIgnoreCase) ||
            a.StartsWith(n + "=", StringComparison.OrdinalIgnoreCase)));

    internal static string? GetOption(string[] args, params string[] names)
    {
        for (int i = 0; i < args.Length; i++)
        {
            foreach (string name in names)
            {
                if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                    return args[i + 1];
                string prefix = name + "=";
                if (args[i].StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    return args[i][prefix.Length..];
            }
        }
        return null;
    }

    internal static bool TruthyEnv(string name)
    {
        string val = (Environment.GetEnvironmentVariable(name) ?? string.Empty).Trim();
        return val.Equals("1", StringComparison.OrdinalIgnoreCase) ||
               val.Equals("true", StringComparison.OrdinalIgnoreCase) ||
               val.Equals("yes", StringComparison.OrdinalIgnoreCase) ||
               val.Equals("on", StringComparison.OrdinalIgnoreCase);
    }

    internal static void PrintBanner(string version)
    {
        string delivery = IsOfflineInstaller(typeof(Program).Assembly, null) ? "Offline Setup" : "Online Setup";
        Console.WriteLine("============================================================");
        Console.WriteLine($"  Greenroom Setup & Self-Contained Installer (v{version}, {delivery})");
        Console.WriteLine("============================================================");
    }

    internal static void PrintUsage()
    {
        Console.WriteLine("\nUsage: Greenroom-Setup.exe [options]");
        Console.WriteLine("  --install-dir|--target-dir <dir>  Target installation directory");
        Console.WriteLine("  --suite-dir <dir>                 Shared Mother suite umbrella directory");
        Console.WriteLine("  --offline-bundle <zipOrDir>       Install Online Setup offline from Portable/payload ZIP");
        Console.WriteLine("  --profile <lightweight|full>      Installation profile (default: full)");
        Console.WriteLine("  --migrate-legacy                  Copy data/config/drops from legacy flat install");
        Console.WriteLine("  --silent|--quiet | --no-pause     Run non-interactive setup without console prompts");
        Console.WriteLine("  --no-shortcuts                    Skip Desktop and Start Menu shortcut creation");
        Console.WriteLine("  --skip-venv | --with-venv         Skip or force Python .venv creation from wheelhouse");
        Console.WriteLine("  --force-pip                       Force full pip wheelhouse reinstall");
        Console.WriteLine("  --autostart                       Configure Windows login autostart if allowed");
        Console.WriteLine("  --no-daemon-restart               Skip restarting Greenroom Companion daemon");
        Console.WriteLine("  --uninstall [--purge-data]        Remove installed binaries and shortcuts");
        Console.WriteLine("  --verify                          Verify manifest/HTTPS pins or embedded payload.zip");
        Console.WriteLine("  --data-dir <dir> --log-dir <dir>  Isolated runtime containment data and log roots");
        Console.WriteLine("  --config <jsonPath>               External JSON installer configuration file");
        Console.WriteLine("  --manifest <jsonPath>             External installer.manifest.json override");
        Console.WriteLine("  --dry-run                         Preview planned actions without modifying host state");
    }
}
