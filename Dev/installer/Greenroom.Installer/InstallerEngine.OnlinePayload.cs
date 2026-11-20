// @module InstallerEngine.OnlinePayload.cs
// @purpose Standard 41 6-pillar HTTPS payload transfer (SSRF/TLS 1.2+, timeouts, jitter retries, Range resume, 64KB .part, SHA-256).
// @inputs Download URL, expected SHA-256/byte size, destination path, and optional test hooks (@depends-on System.Net.Http, System.Security.Cryptography)
// @outputs Atomically verified Portable ZIP path on disk or fail-closed 3-Part Error (@exports Program.DownloadAndVerifyOnlinePayloadAsync, Program.ValidateDownloadUri)

using System.Buffers;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;

namespace Greenroom.Installer;

internal sealed record OnlineTransferTestHooks(
    bool AllowLoopbackHttp = false,
    DualInstallerManifestDto? ManifestOverride = null,
    HttpMessageHandler? CustomHandler = null,
    int MaxRetryDelayMs = 30_000,
    bool AllowUnpinnedOfflineBundle = false);

public static partial class Program
{
    private const int DownloadChunkBytes = 65_536;
    private const long DiskFloorReserveBytes = 512L * 1024 * 1024;

    internal static Uri ValidateDownloadUri(string rawUrl, bool allowLoopbackHttp, bool resolveDns = false)
    {
        if (!Uri.TryCreate(rawUrl?.Trim(), UriKind.Absolute, out Uri? uri))
            throw new InvalidOperationException($"Invalid payload download URL: '{rawUrl}'.");
        bool isLoopbackHost = uri.Host.Equals("127.0.0.1", StringComparison.OrdinalIgnoreCase) ||
                              uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase);

        if (uri.Scheme.Equals(Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase))
        {
            if (!(allowLoopbackHttp && isLoopbackHost))
                throw new InvalidOperationException($"Blocked insecure HTTP download URL '{uri}'; HTTPS (TLS 1.2+) is required.");
        }
        else if (!uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"Blocked unsupported URI scheme '{uri.Scheme}' for '{uri}'.");
        }

        if (IPAddress.TryParse(uri.Host, out IPAddress? literalIp))
        {
            if (IsBlockedIpAddress(literalIp, allowLoopbackHttp))
                throw new InvalidOperationException($"Blocked private or link-local SSRF IP '{literalIp}'.");
        }
        else if (isLoopbackHost && !allowLoopbackHttp)
        {
            throw new InvalidOperationException($"Blocked loopback host '{uri.Host}' without loopback test authorization.");
        }
        else if (resolveDns && !(allowLoopbackHttp && isLoopbackHost))
        {
            try
            {
                foreach (IPAddress ip in Dns.GetHostAddresses(uri.DnsSafeHost))
                    if (IsBlockedIpAddress(ip, allowLoopbackHttp))
                        throw new InvalidOperationException($"Blocked SSRF DNS resolution for '{uri.Host}' -> '{ip}'.");
            }
            catch (SocketException) { /* Offline DNS errors handled cleanly by caller transfer loop */ }
        }
        return uri;
    }

    private static bool IsBlockedIpAddress(IPAddress ip, bool allowLoopback)
    {
        if (ip.IsIPv4MappedToIPv6) ip = ip.MapToIPv4();
        if (IPAddress.IsLoopback(ip)) return !allowLoopback;
        if (ip.Equals(IPAddress.Any) || ip.Equals(IPAddress.IPv6Any) || ip.Equals(IPAddress.Broadcast) || ip.Equals(IPAddress.None)) return true;
        if (ip.IsIPv6LinkLocal || ip.IsIPv6SiteLocal || ip.IsIPv6Multicast) return true;
        byte[] b = ip.GetAddressBytes();
        if (b.Length == 16) return (b[0] & 0xFE) == 0xFC;
        if (b.Length != 4) return true;
        return b[0] == 0 || b[0] == 10 || (b[0] == 100 && b[1] is >= 64 and <= 127) ||
               b[0] == 127 || (b[0] == 169 && b[1] == 254) ||
               (b[0] == 172 && b[1] is >= 16 and <= 31) || (b[0] == 192 && b[1] == 168);
    }

    internal static async Task<string> DownloadAndVerifyOnlinePayloadAsync(
        string downloadUrl, string expectedSha256, long expectedSizeBytes, string destinationZipPath,
        string version, string suiteId, Action<int, string>? onProgress = null,
        OnlineTransferTestHooks? hooks = null, CancellationToken ct = default)
    {
        bool allowLoopback = hooks?.AllowLoopbackHttp == true;
#if DEBUG || GREENROOM_TEST_AUTOMATION
        allowLoopback = allowLoopback || TruthyEnv("GREENROOM_ALLOW_LOOPBACK_HTTP");
#endif
        if (!IsValidSha256Hex(expectedSha256))
            throw new InvalidDataException(FormatThreePartOfflineError(version, "Online manifest is missing a valid 64-character SHA-256 checksum."));

        Uri uri = ValidateDownloadUri(downloadUrl, allowLoopback, resolveDns: true);
        string fullDest = Path.GetFullPath(destinationZipPath);
        string partPath = fullDest + ".part";
        Directory.CreateDirectory(Path.GetDirectoryName(fullDest)!);
        if (expectedSizeBytes > 0) AssertDiskFreeSpace(fullDest, expectedSizeBytes);

        using HttpClient client = CreateOnlineHttpClient(version, suiteId, hooks?.CustomHandler);
        string expectedLower = expectedSha256.Trim().ToLowerInvariant();
        string? initialETag = null;
        bool serverAdvertisedRanges = File.Exists(partPath) && new FileInfo(partPath).Length > 0;
        long? remoteContentLength = null;

        for (int attempt = 0; ; attempt++)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                long existingOffset = serverAdvertisedRanges && File.Exists(partPath) ? new FileInfo(partPath).Length : 0L;
                using HttpResponseMessage resp = await SendWithValidatedRedirectsAsync(client, uri, existingOffset, allowLoopback, ct).ConfigureAwait(false);
                int statusCode = (int)resp.StatusCode;
                if (resp.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable && existingOffset > 0)
                {
                    ForceDeleteFile(partPath);
                    serverAdvertisedRanges = false;
                    if (attempt < 3) continue;
                }
                if (statusCode is 400 or 401 or 403 or 404 or 410 or 422)
                    throw new HttpRequestException($"HTTP {statusCode} ({resp.ReasonPhrase})", null, resp.StatusCode);
                if (statusCode is 408 or 429 or 502 or 503 or 504 || statusCode >= 500)
                {
                    var retryEx = new HttpRequestException($"Transient HTTP {statusCode} ({resp.ReasonPhrase})", null, resp.StatusCode);
                    retryEx.Data["RetryAfter"] = ResolveRetryAfter(resp.Headers.RetryAfter);
                    throw retryEx;
                }
                resp.EnsureSuccessStatusCode();

                bool acceptRanges = resp.Headers.AcceptRanges.Any(v => v.Equals("bytes", StringComparison.OrdinalIgnoreCase));
                string? respETag = resp.Headers.ETag?.Tag;
                bool isAppend = resp.StatusCode == HttpStatusCode.PartialContent && existingOffset > 0 &&
                                (string.IsNullOrEmpty(initialETag) || string.IsNullOrEmpty(respETag) || string.Equals(initialETag, respETag, StringComparison.Ordinal));
                if (!isAppend) { existingOffset = 0; initialETag = respETag; }
                serverAdvertisedRanges = acceptRanges || isAppend;

                long? totalBytes = resp.Content.Headers.ContentLength.HasValue ? resp.Content.Headers.ContentLength.Value + existingOffset : (expectedSizeBytes > 0 ? expectedSizeBytes : null);
                remoteContentLength = totalBytes;
                if (totalBytes.HasValue) AssertDiskFreeSpace(fullDest, totalBytes.Value);

                await StreamResponseToPartAsync(resp, partPath, isAppend, existingOffset, totalBytes, onProgress, ct).ConfigureAwait(false);
                break;
            }
            catch (Exception ex) when (attempt < 3 && IsTransientTransferException(ex, ct))
            {
                TimeSpan delay = ComputeFullJitterDelay(attempt + 1, ex.Data["RetryAfter"] as TimeSpan?, hooks?.MaxRetryDelayMs ?? 30_000);
                onProgress?.Invoke(12, $"Download interrupted ({ex.Message}); retrying in {delay.TotalSeconds:F1}s ({attempt + 1}/3)...");
                await Task.Delay(delay, ct).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException(FormatThreePartOfflineError(version, $"Download from '{uri}' failed: {ex.Message}"), ex);
            }
        }

        long actualLen = new FileInfo(partPath).Length;
        if ((expectedSizeBytes > 0 && actualLen != expectedSizeBytes) || (remoteContentLength.HasValue && actualLen != remoteContentLength.Value))
        {
            ForceDeleteFile(partPath);
            throw new IOException(FormatThreePartOfflineError(version, $"Downloaded archive truncated ({actualLen:N0} bytes received)."));
        }

        string actualSha = await ComputeFileSha256Async(partPath, ct).ConfigureAwait(false);
        if (!string.Equals(actualSha, expectedLower, StringComparison.Ordinal))
        {
            ForceDeleteFile(partPath);
            throw new CryptographicException(FormatThreePartOfflineError(version, $"SHA-256 mismatch: expected {expectedLower}, actual {actualSha}."));
        }

        if (File.Exists(fullDest)) ForceDeleteFile(fullDest);
        File.Move(partPath, fullDest, overwrite: true);
        return fullDest;
    }

    private static async Task<HttpResponseMessage> SendWithValidatedRedirectsAsync(
        HttpClient client, Uri initialUri, long rangeOffset, bool allowLoopback, CancellationToken ct)
    {
        Uri current = initialUri;
        for (int hops = 0; hops < 6; hops++)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, current);
            if (rangeOffset > 0) req.Headers.Range = new RangeHeaderValue(rangeOffset, null);
            using var hdrCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            hdrCts.CancelAfter(TimeSpan.FromSeconds(30));
            HttpResponseMessage resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, hdrCts.Token).ConfigureAwait(false);
            int code = (int)resp.StatusCode;
            if (code is 301 or 302 or 303 or 307 or 308 && resp.Headers.Location is { } loc)
            {
                Uri next = loc.IsAbsoluteUri ? loc : new Uri(current, loc);
                resp.Dispose();
                current = ValidateDownloadUri(next.ToString(), allowLoopback, resolveDns: true);
                continue;
            }
            return resp;
        }
        throw new HttpRequestException("Exceeded maximum HTTP redirect limit (5).");
    }

    private static async Task StreamResponseToPartAsync(
        HttpResponseMessage resp, string partPath, bool append, long initialBytes, long? totalBytes,
        Action<int, string>? onProgress, CancellationToken ct)
    {
        byte[] buffer = ArrayPool<byte>.Shared.Rent(DownloadChunkBytes);
        try
        {
            await using Stream net = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
            await using var fs = new FileStream(partPath, append ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None, DownloadChunkBytes, useAsync: true);
            long written = initialBytes;
            var started = DateTime.UtcNow;
            var lastBeat = DateTime.UtcNow.AddSeconds(-10);

            while (true)
            {
                using var stallCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                stallCts.CancelAfter(TimeSpan.FromSeconds(30));
                int read = await net.ReadAsync(buffer.AsMemory(0, DownloadChunkBytes), stallCts.Token).ConfigureAwait(false);
                if (read <= 0) break;
                await fs.WriteAsync(buffer.AsMemory(0, read), ct).ConfigureAwait(false);
                written += read;
                DateTime now = DateTime.UtcNow;
                if ((now - lastBeat).TotalSeconds >= 2.0)
                {
                    double elapsed = Math.Max(0.1, (now - started).TotalSeconds);
                    double mbps = (written - initialBytes) / (1024.0 * 1024.0) / elapsed;
                    int pct = totalBytes is > 0 ? Math.Clamp(10 + (int)(25.0 * written / totalBytes.Value), 10, 35) : 22;
                    string sizeText = totalBytes is > 0 ? $"{written / 1048576.0:F1}/{totalBytes.Value / 1048576.0:F1} MB" : $"{written / 1048576.0:F1} MB";
                    onProgress?.Invoke(pct, $"Downloading package ({sizeText} at {mbps:F1} MB/s)...");
                    lastBeat = now;
                }
            }
            await fs.FlushAsync(ct).ConfigureAwait(false);
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
    }

    internal static void AssertDiskFreeSpace(string targetPath, long expectedBytes)
    {
        string? root = Path.GetPathRoot(Path.GetFullPath(targetPath));
        if (string.IsNullOrWhiteSpace(root)) return;
        var drive = new DriveInfo(root);
        if (!drive.IsReady) return;
        long required = (long)Math.Ceiling(Math.Max(0L, expectedBytes) * 1.1) + DiskFloorReserveBytes;
        if (drive.AvailableFreeSpace < required)
            throw new IOException($"DISK_FULL: Drive '{root}' has {drive.AvailableFreeSpace / 1048576:N0} MB free; require {required / 1048576:N0} MB.");
    }

    private static HttpClient CreateOnlineHttpClient(string version, string suiteId, HttpMessageHandler? customHandler)
    {
        HttpMessageHandler handler = customHandler ?? new SocketsHttpHandler
        {
            ConnectTimeout = TimeSpan.FromSeconds(10),
            AllowAutoRedirect = false,
            AutomaticDecompression = DecompressionMethods.None,
            SslOptions = new System.Net.Security.SslClientAuthenticationOptions { EnabledSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13 }
        };
        var client = new HttpClient(handler, disposeHandler: customHandler is null) { Timeout = Timeout.InfiniteTimeSpan };
        client.DefaultRequestHeaders.UserAgent.ParseAdd($"Greenroom-Setup/{version} (Windows NT; Suite/{(string.IsNullOrWhiteSpace(suiteId) ? "mother" : suiteId)})");
        return client;
    }

    private static TimeSpan? ResolveRetryAfter(RetryConditionHeaderValue? header) =>
        header?.Delta ?? (header?.Date is { } dt ? dt - DateTimeOffset.UtcNow : null);

    private static TimeSpan ComputeFullJitterDelay(int attempt, TimeSpan? retryAfter, int maxDelayMs)
    {
        if (retryAfter is { TotalMilliseconds: > 0 } ra)
            return TimeSpan.FromMilliseconds(Math.Min(maxDelayMs, ra.TotalMilliseconds));
        double cap = Math.Min(maxDelayMs, 1000.0 * Math.Pow(2, attempt - 1));
        return TimeSpan.FromMilliseconds(Math.Max(25.0, Random.Shared.NextDouble() * cap));
    }

    private static bool IsTransientTransferException(Exception ex, CancellationToken outerCt)
    {
        if (outerCt.IsCancellationRequested) return false;
        if (ex is InvalidOperationException) return false;
        if (ex is IOException ioEx && ioEx.Message.StartsWith("DISK_FULL:", StringComparison.Ordinal)) return false;
        if (ex is HttpRequestException hre && hre.StatusCode is { } sc && (int)sc is 400 or 401 or 403 or 404 or 410 or 422) return false;
        return ex is HttpRequestException or IOException or OperationCanceledException;
    }

    private static async Task<string> ComputeFileSha256Async(string filePath, CancellationToken ct)
    {
        using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buf = ArrayPool<byte>.Shared.Rent(DownloadChunkBytes);
        try
        {
            await using var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read, DownloadChunkBytes, useAsync: true);
            int read;
            while ((read = await fs.ReadAsync(buf.AsMemory(0, DownloadChunkBytes), ct).ConfigureAwait(false)) > 0)
                sha.AppendData(buf, 0, read);
            return Convert.ToHexString(sha.GetHashAndReset()).ToLowerInvariant();
        }
        finally { ArrayPool<byte>.Shared.Return(buf); }
    }
}
