// @module Greenroom.Installer.Tests.Helper
// @purpose Shared ephemeral 4-drive sandbox, mock Portable ZIP, and Local HttpListener server helpers for installer unit tests.
// @inputs Temporary workspace paths and sample payload entries (@depends-on Foundation.Core, Foundation.Installer, System.Net.HttpListener)
// @outputs Isolated test sandboxes, signed/hashed test ZIPs, and scoped env var restorers (@exports InstallerTestFixtureHelper)

using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer.Tests;

internal sealed class InstallerTestFixtureHelper : IDisposable
{
    public string TempRoot { get; }
    private readonly List<(string Key, string? Original)> _savedEnv = [];

    public InstallerTestFixtureHelper(string prefix = "GreenroomInstTest")
    {
        TempRoot = Path.Combine(Path.GetTempPath(), $"{prefix}_{Guid.NewGuid():N}");
        Directory.CreateDirectory(TempRoot);
    }

    public void SetEnv(string key, string? value)
    {
        _savedEnv.Add((key, Environment.GetEnvironmentVariable(key)));
        Environment.SetEnvironmentVariable(key, value);
    }

    public (SandboxResolution Sandbox, InstallerRuntimeContext Context, JsonHivePlatformRegistry Hive, string CDrive, string DDrive) CreateSandboxContext(string name = "sbx")
    {
        string sbxRoot = Path.Combine(TempRoot, name);
        string cDrive = Path.Combine(sbxRoot, "C");
        string dDrive = Path.Combine(sbxRoot, "D");
        string profile = Path.Combine(cDrive, "Users", "Sandbox");
        Directory.CreateDirectory(Path.Combine(profile, "AppData", "Local"));
        Directory.CreateDirectory(Path.Combine(profile, "AppData", "Roaming"));
        Directory.CreateDirectory(Path.Combine(profile, "Desktop"));
        Directory.CreateDirectory(dDrive);

        string hivePath = Path.Combine(sbxRoot, "registry", "registry.hive.json");
        string lockPath = Path.Combine(sbxRoot, "locks", "registry.hive.lock");
        var sandbox = new SandboxResolution(true, sbxRoot, cDrive, dDrive, null, null, profile, hivePath, null, lockPath);
        var hive = new JsonHivePlatformRegistry(hivePath, sbxRoot, lockPath);
        var ctx = new InstallerRuntimeContext(null, null, null, dryRun: false, envPrefix: "GREENROOM", sandboxOverride: sandbox, registryOverride: hive);
        return (sandbox, ctx, hive, cDrive, dDrive);
    }

    public static (byte[] ZipBytes, string Sha256Hex, string FilePath) CreatePortableTestZip(string outputZipPath, bool includeTopDocs = true)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(outputZipPath)!);
        if (File.Exists(outputZipPath)) File.Delete(outputZipPath);

        using (var fs = File.Create(outputZipPath))
        using (var zip = new ZipArchive(fs, ZipArchiveMode.Create))
        {
            WriteZipEntry(zip, "Open Greenroom.bat", "@echo off\r\necho Greenroom\r\n");
            WriteZipEntry(zip, "app/pyproject.toml", "[project]\nname = \"greenroom\"\nversion = \"0.3.0\"\n");
            WriteZipEntry(zip, "app/requirements.txt", "pydantic>=2.6.0\n");
            WriteZipEntry(zip, "app/README.md", "# Embedded App Readme\n");
            WriteZipEntry(zip, "app/suite.manifest.json", "{\"schemaVersion\":2,\"manifestType\":\"suite-app\",\"appId\":\"greenroom\",\"version\":\"0.3.0\"}\n");
            WriteZipEntry(zip, "app/greenroom/__init__.py", "__version__ = \"0.3.0\"\n");
            WriteZipEntry(zip, "scripts/interactive/greenroom_menu.bat", "@echo off\r\n");
            if (includeTopDocs)
            {
                WriteZipEntry(zip, "README.md", "# Top Portable Readme\n");
                WriteZipEntry(zip, "FEATURES.md", "# Features\n");
                WriteZipEntry(zip, "RELEASE_NOTES.md", "# Release Notes\n");
                WriteZipEntry(zip, "CHANGELOG.md", "# Changelog\n");
            }
        }

        byte[] bytes = File.ReadAllBytes(outputZipPath);
        string sha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        return (bytes, sha, outputZipPath);
    }

    public static void WriteZipEntry(ZipArchive zip, string entryName, string content)
    {
        ZipArchiveEntry entry = zip.CreateEntry(entryName, CompressionLevel.Fastest);
        using Stream s = entry.Open();
        byte[] data = Encoding.UTF8.GetBytes(content);
        s.Write(data, 0, data.Length);
    }

    public static int GetFreeLoopbackPort()
    {
        using var tcp = new TcpListener(IPAddress.Loopback, 0);
        tcp.Start();
        return ((IPEndPoint)tcp.LocalEndpoint).Port;
    }

    public void Dispose()
    {
        for (int i = _savedEnv.Count - 1; i >= 0; i--)
        {
            Environment.SetEnvironmentVariable(_savedEnv[i].Key, _savedEnv[i].Original);
        }
        try { if (Directory.Exists(TempRoot)) Directory.Delete(TempRoot, recursive: true); } catch { /* ignore cleanup lock */ }
    }
}
