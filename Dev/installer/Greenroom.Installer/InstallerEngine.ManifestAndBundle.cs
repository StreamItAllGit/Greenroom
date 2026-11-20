// @module InstallerEngine.ManifestAndBundle.cs
// @purpose Delivery kind detection, DualInstallerManifestDto loading, --offline-bundle resolution, ZIP safety, and --verify.
// @inputs Embedded/adjacent installer.manifest.json, AssemblyMetadata, and offline ZIP paths (@depends-on Foundation.Core, Foundation.Installer)
// @outputs Verified delivery metadata, clean-root parity exclusions, and 3-Part Errors (@exports Program.LoadDualInstallerManifest, Program.ResolveOfflineBundlePath, Program.IsCleanRootParityExcluded)

using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    private static readonly HashSet<string> PortableTopDocExclusions = new(StringComparer.OrdinalIgnoreCase)
    {
        "README.md", "FEATURES.md", "RELEASE_NOTES.md", "CHANGELOG.md", "SHA256SUMS.txt", "AGENTS.md"
    };

    internal static string ResolveEffectiveDeliveryKind(
        Assembly? asm = null, DualInstallerManifestDto? manifest = null, string? overrideKind = null)
    {
        if (!string.IsNullOrWhiteSpace(overrideKind))
            return overrideKind.Trim().Equals("offlineSetup", StringComparison.OrdinalIgnoreCase) ? "offlineSetup" : "onlineSetup";

        Assembly targetAsm = asm ?? typeof(Program).Assembly;
        string? metaKind = targetAsm.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(a => string.Equals(a.Key, "Greenroom.InstallerDeliveryKind", StringComparison.OrdinalIgnoreCase))?.Value;
        if (!string.IsNullOrWhiteSpace(metaKind))
            return metaKind.Trim().Equals("offlineSetup", StringComparison.OrdinalIgnoreCase) ? "offlineSetup" : "onlineSetup";

        if (targetAsm.GetManifestResourceInfo(EmbeddedPayloadZipResource) != null)
            return "offlineSetup";
#if GREENROOM_OFFLINE_SETUP
        return "offlineSetup";
#else
        return manifest?.InstallerDeliveryKind?.Trim().Equals("offlineSetup", StringComparison.OrdinalIgnoreCase) == true
            ? "offlineSetup" : "onlineSetup";
#endif
    }

    internal static bool IsOfflineInstaller(Assembly? asm = null, DualInstallerManifestDto? manifest = null, string? overrideKind = null) =>
        string.Equals(ResolveEffectiveDeliveryKind(asm, manifest, overrideKind), "offlineSetup", StringComparison.OrdinalIgnoreCase);

    internal static bool IsCleanRootParityExcluded(string normalizedRelPath) =>
        !string.IsNullOrWhiteSpace(normalizedRelPath) &&
        !normalizedRelPath.Contains('/') &&
        PortableTopDocExclusions.Contains(normalizedRelPath);

    internal static DualInstallerManifestDto? LoadDualInstallerManifest(
        Assembly? asm = null, string? explicitManifestPath = null, InstallerRuntimeContext? runtimeCtx = null)
    {
        Assembly targetAsm = asm ?? typeof(Program).Assembly;
        string? jsonText = null;

#if DEBUG || GREENROOM_TEST_AUTOMATION
        if (!string.IsNullOrWhiteSpace(explicitManifestPath))
        {
            string full = Path.GetFullPath(explicitManifestPath.Trim());
            if (runtimeCtx?.IsSandboxActive == true) full = SandboxEnvironmentSeam.AssertSandboxPathIfActive(full, runtimeCtx.Sandbox);
            jsonText = File.ReadAllText(full, Encoding.UTF8);
        }
        else
        {
            string adjacent = Path.Combine(AppContext.BaseDirectory, "installer.manifest.json");
            if (File.Exists(adjacent)) jsonText = File.ReadAllText(adjacent, Encoding.UTF8);
        }
#else
        _ = explicitManifestPath;
        _ = runtimeCtx;
#endif
        if (jsonText is null)
        {
            Stream? s = targetAsm.GetManifestResourceStream(EmbeddedInstallerManifestResource);
            if (s is null)
            {
                string? alt = targetAsm.GetManifestResourceNames()
                    .FirstOrDefault(n => n.EndsWith("installer.manifest.json", StringComparison.OrdinalIgnoreCase));
                if (!string.IsNullOrWhiteSpace(alt))
                    s = targetAsm.GetManifestResourceStream(alt);
            }
            if (s is null) return null;
            using (s)
            using (var reader = new StreamReader(s, Encoding.UTF8))
            {
                jsonText = reader.ReadToEnd();
            }
        }

        _ = InstallerManifestStore.LoadFromJson(jsonText);
        return JsonSerializer.Deserialize<DualInstallerManifestDto>(jsonText, JsonOpts);
    }

    internal static string ResolveOfflineBundlePath(string zipOrDir, string version, InstallerRuntimeContext? runtimeCtx = null)
    {
        string full = Path.GetFullPath(zipOrDir.Trim());
        if (runtimeCtx?.IsSandboxActive == true) full = SandboxEnvironmentSeam.AssertSandboxPathIfActive(full, runtimeCtx.Sandbox);
        if (File.Exists(full)) return full;
        if (!Directory.Exists(full))
            throw new FileNotFoundException(FormatThreePartOfflineError(version, $"Offline bundle path '{full}' does not exist."));

        string exactPortable = Path.Combine(full, $"Greenroom-v{version}-win-x64-Portable.zip");
        if (File.Exists(exactPortable)) return exactPortable;
        string? anyPortable = Directory.GetFiles(full, "Greenroom-v*-win-x64-Portable.zip", SearchOption.TopDirectoryOnly)
            .OrderByDescending(f => f, StringComparer.OrdinalIgnoreCase).FirstOrDefault();
        if (!string.IsNullOrWhiteSpace(anyPortable)) return anyPortable;
        string payloadZip = Path.Combine(full, "payload.zip");
        if (File.Exists(payloadZip)) return payloadZip;
        throw new FileNotFoundException(FormatThreePartOfflineError(version, $"No Portable or payload.zip found in '{full}'."));
    }

    internal static (bool Valid, string Sha256, long SizeBytes, int EntryCount, string Message) VerifyOnlineOrOffline(
        string? externalPayloadPath, string? offlineBundlePath, string? manifestPath,
        string? payloadUrlOverride, string? payloadShaOverride, Assembly? asm = null,
        InstallerRuntimeContext? runtimeCtx = null, OnlineTransferTestHooks? hooks = null)
    {
        DualInstallerManifestDto? manifest = hooks?.ManifestOverride ?? LoadDualInstallerManifest(asm, manifestPath, runtimeCtx);
        if (!string.IsNullOrWhiteSpace(offlineBundlePath))
        {
            string bundleFile = ResolveOfflineBundlePath(offlineBundlePath, manifest?.Version ?? ResolveProductVersion(), runtimeCtx);
            return VerifyArchiveFile(bundleFile, manifest, hooks?.AllowUnpinnedOfflineBundle == true);
        }
        if (!string.IsNullOrWhiteSpace(externalPayloadPath) || IsOfflineInstaller(asm, manifest))
            return VerifyEmbeddedOrExternalArchive(externalPayloadPath);

        if (manifest is null)
            return (false, string.Empty, 0, 0, "Missing embedded installer.manifest.json for Online Setup verification.");

        string url = ResolveEffectiveDownloadUrl(manifest, payloadUrlOverride);
        PayloadAssetEntry? asset = manifest.PayloadAssets.FirstOrDefault();
        string sha = ResolveEffectiveExpectedSha256(asset, payloadShaOverride);
        long size = asset?.ExpectedSizeBytes ?? 0;
        if (string.IsNullOrWhiteSpace(url))
            return (false, string.Empty, 0, 0, "Online Setup manifest is missing payloadDownloadUrl.");
        try
        {
            bool allowLoopback = hooks?.AllowLoopbackHttp == true;
#if DEBUG || GREENROOM_TEST_AUTOMATION
            allowLoopback = allowLoopback || TruthyEnv("GREENROOM_ALLOW_LOOPBACK_HTTP");
#endif
            ValidateDownloadUri(url, allowLoopback, resolveDns: false);
        }
        catch (Exception ex) { return (false, string.Empty, 0, 0, ex.Message); }

        if (!IsValidSha256Hex(sha))
            return (false, string.Empty, 0, 0, "Online Setup manifest requires a valid 64-character lowercase SHA-256 pin in payloadAssets[].");
        if (size <= 0)
            return (false, sha.ToLowerInvariant(), 0, 0, "Online Setup manifest requires expectedSizeBytes > 0 in payloadAssets[].");
        return (true, sha.ToLowerInvariant(), size, 1, $"Verified Online Setup manifest v{manifest.Version} ({url}, {size:N0} bytes, SHA-256: {sha.ToLowerInvariant()}).");
    }

    internal static string ResolveEffectiveDownloadUrl(DualInstallerManifestDto? manifest, string? overrideUrl)
    {
#if DEBUG || GREENROOM_TEST_AUTOMATION
        if (!string.IsNullOrWhiteSpace(overrideUrl)) return overrideUrl.Trim();
#else
        _ = overrideUrl;
#endif
        return manifest?.PayloadDownloadUrl?.Trim() ?? string.Empty;
    }

    internal static string ResolveEffectiveExpectedSha256(PayloadAssetEntry? asset, string? overrideSha)
    {
#if DEBUG || GREENROOM_TEST_AUTOMATION
        if (!string.IsNullOrWhiteSpace(overrideSha)) return overrideSha.Trim().ToLowerInvariant();
#else
        _ = overrideSha;
#endif
        return asset?.ExpectedSha256?.Trim().ToLowerInvariant() ?? string.Empty;
    }

    internal static (bool Valid, string Sha256, long SizeBytes, int EntryCount, string Message) VerifyArchiveFile(
        string archivePath, DualInstallerManifestDto? manifest, bool allowUnpinned, string? expectedShaOverride = null)
    {
        if (!File.Exists(archivePath)) return (false, string.Empty, 0, 0, $"Offline bundle file not found: '{archivePath}'.");
        using FileStream fs = File.OpenRead(archivePath);
        string actualSha = Convert.ToHexString(SHA256.HashData(fs)).ToLowerInvariant();
        long size = fs.Length;
        string expectedSha = ResolveEffectiveExpectedSha256(manifest?.PayloadAssets.FirstOrDefault(), expectedShaOverride);
        if (!allowUnpinned && IsValidSha256Hex(expectedSha) && !string.Equals(actualSha, expectedSha, StringComparison.OrdinalIgnoreCase))
            return (false, actualSha, size, 0, FormatThreePartOfflineError(manifest?.Version ?? ResolveProductVersion(), $"Offline bundle SHA-256 mismatch (expected {expectedSha}, actual {actualSha})."));

        fs.Position = 0;
        int entries = InspectAndValidateZipEntries(fs);
        return (true, actualSha, size, entries, $"Verified offline bundle '{Path.GetFileName(archivePath)}' ({size:N0} bytes, {entries} entries, SHA-256: {actualSha}).");
    }

    internal static int InspectAndValidateZipEntries(Stream zipStream)
    {
        using var zip = new ZipArchive(zipStream, ZipArchiveMode.Read, leaveOpen: true);
        int count = 0;
        foreach (ZipArchiveEntry entry in zip.Entries)
        {
            if (string.IsNullOrWhiteSpace(entry.FullName) || entry.FullName.EndsWith('/')) continue;
            ValidateZipEntryPath(entry);
            count++;
        }
        return count == 0 ? throw new InvalidDataException("Installer archive contains zero entries.") : count;
    }

    internal static void ValidateZipEntryPath(ZipArchiveEntry entry)
    {
        if (((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
            throw new InvalidDataException($"Rejected symlink entry '{entry.FullName}' in archive.");
        string raw = entry.FullName.Replace('\\', '/');
        if (raw.StartsWith('/') || (raw.Length >= 2 && raw[1] == ':') || raw.Split('/').Any(s => s == ".."))
            throw new InvalidDataException($"Rejected unsafe archive entry path '{entry.FullName}'.");
    }

    internal static bool IsValidSha256Hex(string? hex) =>
        !string.IsNullOrWhiteSpace(hex) && hex.Trim().Length == 64 && hex.Trim().All(Uri.IsHexDigit);

    internal static string FormatThreePartOfflineError(string version, string reason) =>
        $"What failed: Greenroom v{version} setup could not verify or retrieve the release package. " +
        $"Why: {reason} " +
        $"Next step: Download 'Greenroom-Offline-Setup-v{version}-win-x64.exe' or pass '--offline-bundle <path-to-Greenroom-v{version}-win-x64-Portable.zip>'.";
}
