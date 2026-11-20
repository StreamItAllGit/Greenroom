// @module InstallerEngine.Shortcuts.cs
// @purpose Desktop/Start Menu .lnk shortcut generation via KnownFolderPaths, IPlatformRegistry Uninstall writer, and sandbox-gated autostart.
// @inputs Target install directory, uninstaller path, version, and InstallerRuntimeContext (@depends-on Foundation.Core, Foundation.Installer)
// @outputs Created .lnk paths, Uninstall registry record, and Startup VBS record (@exports Program.CreateShortcuts, WindowsShortcutWriter)

using System.Text;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    private static List<string> CreateShortcuts(
        string installDir,
        string uninstallerPath,
        InstallerRuntimeContext runtimeCtx,
        string? activeDataDir)
    {
        var created = new List<string>();
        string desktopDir;
        string startMenuDir;

        if (runtimeCtx.ShouldSuppressHostMutations)
        {
            string containedBase = activeDataDir ?? installDir;
            desktopDir = Path.Combine(containedBase, "shortcuts", "desktop");
            startMenuDir = Path.Combine(containedBase, "shortcuts", "startmenu", "Greenroom");
        }
        else
        {
            desktopDir = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.DesktopDirectory, GetEffectiveSandbox(runtimeCtx));
            startMenuDir = Path.Combine(
                KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.Programs, GetEffectiveSandbox(runtimeCtx)),
                "Greenroom");
            if (runtimeCtx.IsSandboxActive)
            {
                desktopDir = SandboxEnvironmentSeam.AssertSandboxPathIfActive(desktopDir, runtimeCtx.Sandbox);
                startMenuDir = SandboxEnvironmentSeam.AssertSandboxPathIfActive(startMenuDir, runtimeCtx.Sandbox);
            }
        }

        Directory.CreateDirectory(desktopDir);
        Directory.CreateDirectory(startMenuDir);

        string openBat = Path.Combine(installDir, "Open Greenroom.bat");
        string menuBat = Path.Combine(installDir, "scripts", "interactive", "greenroom_menu.bat");
        if (!File.Exists(menuBat))
            menuBat = Path.Combine(installDir, "scripts", "greenroom_menu.bat");

        string customIco = Path.Combine(installDir, "app", "greenroom", "companion", "static", "icons", "greenroom.ico");
        if (!File.Exists(customIco))
            customIco = Path.Combine(installDir, "greenroom", "companion", "static", "icons", "greenroom.ico");
        string iconLoc = File.Exists(customIco) ? customIco : "";

        if (File.Exists(openBat))
        {
            string deskLnk = Path.Combine(desktopDir, "Greenroom Companion.lnk");
            string startLnk = Path.Combine(startMenuDir, "Greenroom Companion.lnk");
            WindowsShortcutWriter.WriteSimpleLink(deskLnk, openBat, installDir, "Open Greenroom Companion Desktop & Mobile PWA Hub", iconLocation: iconLoc);
            WindowsShortcutWriter.WriteSimpleLink(startLnk, openBat, installDir, "Open Greenroom Companion Desktop & Mobile PWA Hub", iconLocation: iconLoc);
            created.Add(deskLnk);
            created.Add(startLnk);
        }

        if (File.Exists(menuBat))
        {
            string cliLnk = Path.Combine(startMenuDir, "Greenroom Console Menu.lnk");
            WindowsShortcutWriter.WriteSimpleLink(cliLnk, menuBat, installDir, "Launch Greenroom Interactive Task & Scheduler Menu", iconLocation: iconLoc);
            created.Add(cliLnk);
        }

        if (File.Exists(uninstallerPath))
        {
            string unLnk = Path.Combine(startMenuDir, "Uninstall Greenroom.lnk");
            WindowsShortcutWriter.WriteSimpleLink(unLnk, uninstallerPath, installDir, "Uninstall Greenroom", "--uninstall");
            created.Add(unLnk);
        }

        return created;
    }

    private static string? RegisterUninstaller(
        string installDir,
        string uninstallerPath,
        string version,
        InstallerRuntimeContext runtimeCtx)
    {
        if (runtimeCtx.ShouldSuppressHostMutations ||
            runtimeCtx.Registry is NoOpPlatformRegistry ||
            (!OperatingSystem.IsWindows() && !runtimeCtx.IsSandboxActive))
        {
            return null;
        }

        string uninstallCmd = $"\"{uninstallerPath}\" --uninstall";
        string quietUninstallCmd = $"\"{uninstallerPath}\" --uninstall --silent";

        runtimeCtx.Registry.SetValues(
            PlatformRegistryHive.CurrentUser,
            WindowsUninstallRegistrySubKey,
            new Dictionary<string, PlatformRegistryValue>
            {
                ["DisplayName"] = new(PlatformRegistryValueKind.String, "Greenroom"),
                ["DisplayVersion"] = new(PlatformRegistryValueKind.String, version),
                ["Publisher"] = new(PlatformRegistryValueKind.String, "StreamItAll"),
                ["InstallLocation"] = new(PlatformRegistryValueKind.String, installDir),
                ["DisplayIcon"] = new(PlatformRegistryValueKind.String, uninstallerPath),
                ["UninstallString"] = new(PlatformRegistryValueKind.String, uninstallCmd),
                ["QuietUninstallString"] = new(PlatformRegistryValueKind.String, quietUninstallCmd),
                ["NoModify"] = new(PlatformRegistryValueKind.DWord, 1),
                ["NoRepair"] = new(PlatformRegistryValueKind.DWord, 1)
            });

        return $"HKCU\\{WindowsUninstallRegistrySubKey}";
    }

    private static string ConfigureAutostart(
        string installDir,
        InstallerRuntimeContext runtimeCtx,
        string? activeDataDir)
    {
        string pywExe = File.Exists(Path.Combine(installDir, "app", ".venv", "Scripts", "pythonw.exe"))
            ? Path.Combine(installDir, "app", ".venv", "Scripts", "pythonw.exe")
            : Path.Combine(installDir, ".venv", "Scripts", "pythonw.exe");
        string pyExe = File.Exists(Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe"))
            ? Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe")
            : Path.Combine(installDir, ".venv", "Scripts", "python.exe");
        string startBat = Path.Combine(installDir, "scripts", "companion_start.bat");
        string escapedRoot = installDir.Replace("\"", "\"\"");
        string runLine = File.Exists(pywExe)
            ? $"WshShell.Run \"\"\"{pywExe.Replace("\"", "\"\"")}\"\" -u -m greenroom.cli companion start --no-pause\", 0, False\r\n"
            : $"WshShell.Run \"cmd.exe /c \"\"\"\"{startBat.Replace("\"", "\"\"")}\"\" --no-pause\"\"\", 0, False\r\n";
        string vbsBody = "Set WshShell = CreateObject(\"WScript.Shell\")\r\n" +
                         $"WshShell.CurrentDirectory = \"{escapedRoot}\"\r\n" +
                         runLine;

        string targetVbs = runtimeCtx.ShouldSuppressHostMutations
            ? Path.Combine(activeDataDir ?? installDir, "GreenroomCompanion.vbs")
            : Path.Combine(KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.Startup, GetEffectiveSandbox(runtimeCtx)), "GreenroomCompanion.vbs");

        if (runtimeCtx.IsSandboxActive)
            targetVbs = SandboxEnvironmentSeam.AssertSandboxPathIfActive(targetVbs, runtimeCtx.Sandbox);

        string? parent = Path.GetDirectoryName(targetVbs);
        if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
        File.WriteAllText(targetVbs, vbsBody, Encoding.UTF8);

        if (!runtimeCtx.ShouldSuppressLiveHostMutations && OperatingSystem.IsWindows() && File.Exists(pyExe))
        {
            try { RunProcess(pyExe, ["-m", "greenroom.cli", "companion", "enable-autostart", "--no-pause"], installDir, 30_000); }
            catch { /* Non-fatal if Task Scheduler service is unavailable */ }
        }

        return targetVbs;
    }
}

internal static class WindowsShortcutWriter
{
    private static readonly Guid ClsidShellLink = new("00021401-0000-0000-C000-000000000046");

    public static void WriteSimpleLink(
        string outputLnkPath, string targetPath, string workingDirectory,
        string description, string arguments = "", string iconLocation = "")
    {
        bool hasArgs = !string.IsNullOrWhiteSpace(arguments);
        bool hasIcon = !string.IsNullOrWhiteSpace(iconLocation);
        uint linkFlags = 0x00000004u | 0x00000008u | 0x00000010u | 0x00000080u | 0x00000200u | 0x00080000u;
        if (hasArgs) linkFlags |= 0x00000020u;
        if (hasIcon) linkFlags |= 0x00000040u;

        using var fs = new FileStream(outputLnkPath, FileMode.Create, FileAccess.Write, FileShare.None);
        using var bw = new BinaryWriter(fs, Encoding.Unicode);

        bw.Write(0x0000004Cu);
        bw.Write(ClsidShellLink.ToByteArray());
        bw.Write(linkFlags);
        bw.Write(0x00000020u);
        bw.Write(0UL);
        bw.Write(0UL);
        bw.Write(0UL);
        bw.Write(0u);
        bw.Write(0);
        bw.Write(0x00000001u);
        bw.Write((ushort)0);
        bw.Write((ushort)0);
        bw.Write(0u);
        bw.Write(0u);

        string relPath = Path.GetRelativePath(Path.GetDirectoryName(outputLnkPath) ?? workingDirectory, targetPath);
        WriteCountedUnicode(bw, description);
        WriteCountedUnicode(bw, relPath);
        WriteCountedUnicode(bw, workingDirectory);
        if (hasArgs) WriteCountedUnicode(bw, arguments);
        if (hasIcon) WriteCountedUnicode(bw, iconLocation);

        bw.Write(0x00000314u);
        bw.Write(0xA0000001u);
        WriteFixedAnsi(bw, targetPath, 260);
        WriteFixedUnicode(bw, targetPath, 260);
        bw.Write(0x00000000u);
    }

    private static void WriteCountedUnicode(BinaryWriter bw, string value)
    {
        string trimmed = value.Length > 32767 ? value[..32767] : value;
        bw.Write((ushort)trimmed.Length);
        bw.Write(Encoding.Unicode.GetBytes(trimmed));
    }

    private static void WriteFixedAnsi(BinaryWriter bw, string value, int byteLen)
    {
        byte[] buf = new byte[byteLen];
        byte[] src = Encoding.ASCII.GetBytes(value);
        Array.Copy(src, buf, Math.Min(src.Length, byteLen - 1));
        bw.Write(buf);
    }

    private static void WriteFixedUnicode(BinaryWriter bw, string value, int charLen)
    {
        byte[] buf = new byte[charLen * 2];
        byte[] src = Encoding.Unicode.GetBytes(value);
        Array.Copy(src, buf, Math.Min(src.Length, buf.Length - 2));
        bw.Write(buf);
    }
}
