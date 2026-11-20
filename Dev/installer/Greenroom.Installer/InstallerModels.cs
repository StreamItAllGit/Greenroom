// @module InstallerModels.cs
// @purpose Data transfer models, Dual-Installer manifest DTOs, path resolution records, and execution options.
// @inputs Embedded payload-meta.json, installer.manifest.json, install-manifest.json, and CLI/JSON flags (@depends-on System.Text.Json)
// @outputs Strongly typed PayloadMetadata, DualInstallerManifestDto, InstallManifest, InstallerConfigFile, and InstallerOptions (@exports PayloadMetadata, DualInstallerManifestDto, InstallManifest, InstallerOptions)

using System.Text.Json.Serialization;

namespace Greenroom.Installer;

public sealed class PayloadMetadata
{
    [JsonPropertyName("product_name")]
    public string ProductName { get; set; } = "Greenroom";

    [JsonPropertyName("version")]
    public string Version { get; set; } = "0.3.0";

    [JsonPropertyName("payload_sha256")]
    public string PayloadSha256 { get; set; } = string.Empty;

    [JsonPropertyName("payload_size_bytes")]
    public long PayloadSizeBytes { get; set; }

    [JsonPropertyName("entry_count")]
    public int EntryCount { get; set; }

    [JsonPropertyName("wheel_count")]
    public int WheelCount { get; set; }

    [JsonPropertyName("built_at_utc")]
    public string BuiltAtUtc { get; set; } = string.Empty;
}

public sealed class PayloadAssetEntry
{
    [JsonPropertyName("installProfile")]
    public string InstallProfile { get; set; } = "full";

    [JsonPropertyName("fileName")]
    public string FileName { get; set; } = string.Empty;

    [JsonPropertyName("expectedSizeBytes")]
    public long ExpectedSizeBytes { get; set; }

    [JsonPropertyName("expectedSha256")]
    public string ExpectedSha256 { get; set; } = string.Empty;

    [JsonPropertyName("relativeSubdir")]
    public string RelativeSubdir { get; set; } = string.Empty;
}

public sealed class DualInstallerManifestDto
{
    [JsonPropertyName("$schema")]
    public string SchemaUri { get; set; } = "https://schemas.local/manifest.v2.json";

    [JsonPropertyName("schemaVersion")]
    public int SchemaVersion { get; set; } = 2;

    [JsonPropertyName("manifestType")]
    public string ManifestType { get; set; } = "installer-manifest";

    [JsonPropertyName("appId")]
    public string AppId { get; set; } = "greenroom";

    [JsonPropertyName("version")]
    public string Version { get; set; } = "0.3.0";

    [JsonPropertyName("updatedAtUtc")]
    public string UpdatedAtUtc { get; set; } = string.Empty;

    [JsonPropertyName("displayName")]
    public string? DisplayName { get; set; }

    [JsonPropertyName("publisher")]
    public string? Publisher { get; set; }

    [JsonPropertyName("subtitle")]
    public string? Subtitle { get; set; }

    [JsonPropertyName("studioName")]
    public string? StudioName { get; set; }

    [JsonPropertyName("suiteId")]
    public string? SuiteId { get; set; }

    [JsonPropertyName("umbrellaSubpath")]
    public string? UmbrellaSubpath { get; set; }

    [JsonPropertyName("installerDeliveryKind")]
    public string InstallerDeliveryKind { get; set; } = "onlineSetup";

    [JsonPropertyName("payloadDownloadUrl")]
    public string? PayloadDownloadUrl { get; set; }

    [JsonPropertyName("payloadAssets")]
    public List<PayloadAssetEntry> PayloadAssets { get; set; } = [];
}

public sealed class InstallManifest
{
    [JsonPropertyName("product_name")]
    public string ProductName { get; set; } = "Greenroom";

    [JsonPropertyName("appId")]
    public string? AppId { get; set; }

    [JsonPropertyName("version")]
    public string Version { get; set; } = "0.3.0";

    [JsonPropertyName("installed_at_utc")]
    public DateTimeOffset InstalledAtUtc { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("install_directory")]
    public string InstallDirectory { get; set; } = string.Empty;

    [JsonPropertyName("payload_sha256")]
    public string PayloadSha256 { get; set; } = string.Empty;

    [JsonPropertyName("managed_directories")]
    public List<string> ManagedDirectories { get; set; } = ["app", "greenroom", "scripts", "wheelhouse"];

    [JsonPropertyName("managed_files")]
    public List<string> ManagedFiles { get; set; } = [];

    [JsonPropertyName("created_shortcuts")]
    public List<string> CreatedShortcuts { get; set; } = [];

    [JsonPropertyName("uninstall_registry_record")]
    public string? UninstallRegistryRecord { get; set; }

    [JsonPropertyName("autostart_record")]
    public string? AutostartRecord { get; set; }

    [JsonPropertyName("mother_manifest_record")]
    public string? MotherManifestRecord { get; set; }

    [JsonPropertyName("umbrella_subpath")]
    public string? UmbrellaSubpath { get; set; }
}

public sealed class InstallerConfigFile
{
    [JsonPropertyName("install_dir")]
    public string? InstallDir { get; set; }

    [JsonPropertyName("create_shortcuts")]
    public bool? CreateShortcuts { get; set; }

    [JsonPropertyName("skip_venv")]
    public bool? SkipVenv { get; set; }

    [JsonPropertyName("autostart")]
    public bool? Autostart { get; set; }

    [JsonPropertyName("suite_dir")]
    public string? SuiteDir { get; set; }

    [JsonPropertyName("profile_id")]
    public string? ProfileId { get; set; }

    [JsonPropertyName("migrate_legacy")]
    public bool? MigrateLegacy { get; set; }

    [JsonPropertyName("offline_bundle")]
    public string? OfflineBundle { get; set; }

    [JsonPropertyName("manifest_path")]
    public string? ManifestPath { get; set; }

    [JsonPropertyName("payload_url")]
    public string? PayloadUrl { get; set; }

    [JsonPropertyName("payload_sha256")]
    public string? PayloadSha256 { get; set; }
}

public sealed record InstallTargetResolution(
    string InstallDir,
    string? MotherSuiteDir,
    bool IsMotherSuiteMode,
    string UmbrellaSubpath,
    string ResolutionStep);

public sealed class InstallerOptions
{
    public string Version { get; init; } = "0.3.0";
    public string? ExplicitInstallDir { get; init; }
    public string? ExternalPayloadPath { get; init; }
    public string? OfflineBundlePath { get; init; }
    public string? ManifestPath { get; init; }
    public string? PayloadUrlOverride { get; init; }
    public string? PayloadSha256Override { get; init; }
    public bool CreateShortcuts { get; init; } = true;
    public bool SkipVenv { get; init; }
    public bool ForcePip { get; init; }
    public bool EnableAutostart { get; init; }
    public bool NoDaemonRestart { get; init; }
    public bool PurgeData { get; init; }
    public bool ContainedMode { get; init; }
    public bool TestMode { get; init; }
    public bool DryRun { get; init; }
    public bool MigrateLegacy { get; init; }
    public bool IsOfflineMode { get; init; }
    public bool? FullSuiteToggle { get; init; }
    public string? SuiteDir { get; init; }
    public string? ProfileId { get; init; }
    public string? ActiveDataDir { get; init; }
    public string? ActiveAppHome { get; init; }
    public string? ActiveLogDir { get; init; }
}
