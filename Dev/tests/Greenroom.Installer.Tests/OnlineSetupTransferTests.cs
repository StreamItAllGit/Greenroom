// @module Greenroom.Installer.Tests.OnlineSetupTransfer
// @purpose Local 127.0.0.1 HttpListener tests for Online Setup streaming, Range resume, 404 0-retry, tampered SHA-256, SSRF, and --offline-bundle parity.
// @inputs Program.DownloadAndVerifyOnlinePayloadAsync, Program.RunInstall, and InstallerTestFixtureHelper (@depends-on Xunit, System.Net.HttpListener)
// @outputs Verified Standard 41 6-pillar download, Zip-Slip/symlink rejection, and clean-root parity (@exports OnlineSetupTransferTests)

using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using Foundation.Installer;
using Xunit;

namespace Greenroom.Installer.Tests;

public sealed class OnlineSetupTransferTests
{
    [Fact]
    public void ValidateDownloadUri_BlocksInsecureAndSsrfUrls_AllowsLoopbackOnlyWithHook()
    {
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("http://github.com/file.zip", allowLoopbackHttp: false));
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("http://127.0.0.1:8080/file.zip", allowLoopbackHttp: false));
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("http://169.254.169.254/latest/meta-data", allowLoopbackHttp: true));
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("https://192.168.1.10/file.zip", allowLoopbackHttp: true));
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("https://10.0.0.1/file.zip", allowLoopbackHttp: true));
        Assert.Throws<InvalidOperationException>(() => Program.ValidateDownloadUri("file:///C:/Windows/win.ini", allowLoopbackHttp: true));

        Uri okHttps = Program.ValidateDownloadUri("https://github.com/StreamItAllGit/Greenroom/releases/download/v0.3.0/Greenroom-v0.3.0-win-x64-Portable.zip", allowLoopbackHttp: false);
        Assert.Equal("https", okHttps.Scheme);
        Uri okLoopback = Program.ValidateDownloadUri("http://127.0.0.1:51234/Portable.zip", allowLoopbackHttp: true);
        Assert.Equal("127.0.0.1", okLoopback.Host);
    }

    [Fact]
    public async Task OnlineSetup_HttpListener_HappyPath_RangeResume_404ZeroRetry_And_TamperedHash()
    {
        using var fx = new InstallerTestFixtureHelper();
        fx.SetEnv("APP_ISOLATED_TEST", null);
        (_, InstallerRuntimeContext ctx, _, _, string dDrive) = fx.CreateSandboxContext();

        (byte[] zipBytes, string sha256, string srcZipPath) = InstallerTestFixtureHelper.CreatePortableTestZip(
            Path.Combine(fx.TempRoot, "Greenroom-v0.3.0-win-x64-Portable.zip"), includeTopDocs: true);

        int port = InstallerTestFixtureHelper.GetFreeLoopbackPort();
        string baseUrl = $"http://127.0.0.1:{port}/";
        int missingHits = 0;
        int transientHits = 0;

        using var listener = new HttpListener();
        listener.Prefixes.Add(baseUrl);
        listener.Start();

        using var serverCts = new CancellationTokenSource();
        Task serverTask = Task.Run(async () =>
        {
            while (!serverCts.IsCancellationRequested)
            {
                HttpListenerContext httpCtx;
                try { httpCtx = await listener.GetContextAsync(); }
                catch { break; }

                string path = httpCtx.Request.Url?.AbsolutePath ?? "/";
                if (path == "/missing.zip")
                {
                    Interlocked.Increment(ref missingHits);
                    httpCtx.Response.StatusCode = 404;
                    httpCtx.Response.Close();
                }
                else if (path == "/retry.zip" && Interlocked.Increment(ref transientHits) == 1)
                {
                    httpCtx.Response.StatusCode = 503;
                    httpCtx.Response.AddHeader("Retry-After", "0");
                    httpCtx.Response.Close();
                }
                else
                {
                    httpCtx.Response.AddHeader("Accept-Ranges", "bytes");
                    httpCtx.Response.AddHeader("ETag", "\"greenroom-v030\"");
                    string? rangeHdr = httpCtx.Request.Headers["Range"];
                    if (!string.IsNullOrWhiteSpace(rangeHdr) && rangeHdr.StartsWith("bytes=", StringComparison.OrdinalIgnoreCase) &&
                        long.TryParse(rangeHdr["bytes=".Length..].TrimEnd('-'), out long offset) && offset > 0 && offset < zipBytes.Length)
                    {
                        byte[] slice = zipBytes[(int)offset..];
                        httpCtx.Response.StatusCode = 206;
                        httpCtx.Response.AddHeader("Content-Range", $"bytes {offset}-{zipBytes.Length - 1}/{zipBytes.Length}");
                        httpCtx.Response.ContentLength64 = slice.Length;
                        await httpCtx.Response.OutputStream.WriteAsync(slice);
                        httpCtx.Response.Close();
                    }
                    else
                    {
                        httpCtx.Response.StatusCode = 200;
                        httpCtx.Response.ContentLength64 = zipBytes.Length;
                        await httpCtx.Response.OutputStream.WriteAsync(zipBytes);
                        httpCtx.Response.Close();
                    }
                }
            }
        });

        try
        {
            var manifest = new DualInstallerManifestDto
            {
                Version = "0.3.0",
                SuiteId = "mother",
                InstallerDeliveryKind = "onlineSetup",
                PayloadDownloadUrl = $"{baseUrl}Portable.zip",
                PayloadAssets = [new PayloadAssetEntry { FileName = Path.GetFileName(srcZipPath), ExpectedSizeBytes = zipBytes.Length, ExpectedSha256 = sha256 }]
            };
            var hooks = new OnlineTransferTestHooks(AllowLoopbackHttp: true, ManifestOverride: manifest, MaxRetryDelayMs: 50);

            // 1. Happy path Online Setup + clean-root parity filter (top Portable docs excluded, app/README.md retained)
            string onlineInstallDir = Path.Combine(dDrive, "OnlineGreenroom");
            int ok = Program.RunInstall(
                "0.3.0", onlineInstallDir, null, createShortcuts: false, skipVenv: true, forcePip: false,
                enableAutostart: false, noDaemonRestart: true, dryRun: false, null, null, null,
                runtimeCtx: ctx, transferHooks: hooks);
            Assert.Equal(0, ok);
            Assert.True(File.Exists(Path.Combine(onlineInstallDir, "Open Greenroom.bat")));
            Assert.True(File.Exists(Path.Combine(onlineInstallDir, "app", "README.md")));
            Assert.False(File.Exists(Path.Combine(onlineInstallDir, "README.md")));
            Assert.False(File.Exists(Path.Combine(onlineInstallDir, "FEATURES.md")));
            Assert.False(File.Exists(Path.Combine(onlineInstallDir, "RELEASE_NOTES.md")));
            Assert.False(File.Exists(Path.Combine(onlineInstallDir, "CHANGELOG.md")));

            // 2. HTTP Range resume from pre-seeded .part file + transient 503 retry
            string resumeDest = Path.Combine(fx.TempRoot, "resumed.zip");
            int half = zipBytes.Length / 2;
            await File.WriteAllBytesAsync(resumeDest + ".part", zipBytes[..half]);
            string downloaded = await Program.DownloadAndVerifyOnlinePayloadAsync(
                $"{baseUrl}retry.zip", sha256, zipBytes.Length, resumeDest, "0.3.0", "mother", hooks: hooks);
            Assert.Equal(sha256, Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(downloaded))).ToLowerInvariant());
            Assert.Equal(2, transientHits);

            // 3. 404 deterministic 0-retry fail-closed with 3-Part Error
            var ex404 = await Assert.ThrowsAsync<InvalidOperationException>(() =>
                Program.DownloadAndVerifyOnlinePayloadAsync(
                    $"{baseUrl}missing.zip", sha256, zipBytes.Length, Path.Combine(fx.TempRoot, "404.zip"), "0.3.0", "mother", hooks: hooks));
            Assert.Equal(1, missingHits);
            Assert.Contains("Greenroom-Offline-Setup-v0.3.0-win-x64.exe", ex404.Message);
            Assert.Contains("Portable.zip", ex404.Message);

            // 4. Tampered SHA-256 fail-closed cleans up .part
            string badHashDest = Path.Combine(fx.TempRoot, "tampered.zip");
            string wrongSha = new string('0', 64);
            await Assert.ThrowsAsync<CryptographicException>(() =>
                Program.DownloadAndVerifyOnlinePayloadAsync(
                    $"{baseUrl}Portable.zip", wrongSha, zipBytes.Length, badHashDest, "0.3.0", "mother", hooks: hooks));
            Assert.False(File.Exists(badHashDest));
            Assert.False(File.Exists(badHashDest + ".part"));

            // 5. --offline-bundle directory resolution + clean-root parity + --verify checks
            string bundleDir = Path.Combine(dDrive, "bundle_dir");
            Directory.CreateDirectory(bundleDir);
            File.Copy(srcZipPath, Path.Combine(bundleDir, Path.GetFileName(srcZipPath)));
            string offlineInstallDir = Path.Combine(dDrive, "OfflineBundleGreenroom");
            int bundleOk = Program.RunInstall(
                "0.3.0", offlineInstallDir, null, false, true, false, false, true, false, null, null, null,
                runtimeCtx: ctx, offlineBundlePath: bundleDir, transferHooks: hooks);
            Assert.Equal(0, bundleOk);
            Assert.False(File.Exists(Path.Combine(offlineInstallDir, "README.md")));
            Assert.True(File.Exists(Path.Combine(offlineInstallDir, "app", "README.md")));

            var vOk = Program.VerifyOnlineOrOffline(null, null, null, null, null, typeof(Program).Assembly, ctx, hooks);
            Assert.True(vOk.Valid);
            var vBadManifest = new DualInstallerManifestDto { Version = "0.3.0", PayloadDownloadUrl = "https://github.com/a/b.zip", PayloadAssets = [new PayloadAssetEntry { ExpectedSizeBytes = 0, ExpectedSha256 = "" }] };
            var vFail = Program.VerifyOnlineOrOffline(null, null, null, null, null, typeof(Program).Assembly, ctx, new OnlineTransferTestHooks(ManifestOverride: vBadManifest));
            Assert.False(vFail.Valid);
        }
        finally
        {
            serverCts.Cancel();
            listener.Stop();
            await serverTask;
        }
    }

    [Fact]
    public void ZipExtraction_RejectsZipSlipAndSymlinks()
    {
        using var ms = new MemoryStream();
        using (var zip = new ZipArchive(ms, ZipArchiveMode.Create, leaveOpen: true))
        {
            InstallerTestFixtureHelper.WriteZipEntry(zip, "../escape.txt", "evil");
        }
        ms.Position = 0;
        Assert.Throws<InvalidDataException>(() => Program.InspectAndValidateZipEntries(ms));

        using var symMs = new MemoryStream();
        using (var zip = new ZipArchive(symMs, ZipArchiveMode.Create, leaveOpen: true))
        {
            ZipArchiveEntry e = zip.CreateEntry("link.txt");
            e.ExternalAttributes = (0xA000 | 0x01FF) << 16;
        }
        symMs.Position = 0;
        Assert.Throws<InvalidDataException>(() => Program.InspectAndValidateZipEntries(symMs));
    }
}
