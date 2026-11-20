// @module InstallerEngine.Extraction.cs
// @purpose Clean Release Root directory cleanup (including AGENTS.md purge), clean-root parity extraction, and template seeding.
// @inputs Target install directory, ZIP archive stream, and managedTopFiles set (@depends-on InstallerEngine.ManifestAndBundle.cs)
// @outputs Safely extracted Clean Release Root without symlinks, Zip-Slip, or loose Portable release docs (@exports Program.PrepareManagedDirectoriesForExtraction, Program.ExtractPayloadToInstallDirectory)

using System.IO.Compression;

namespace Greenroom.Installer;

public static partial class Program
{
    internal static void PrepareManagedDirectoriesForExtraction(string installDir)
    {
        foreach (string dirName in new[] { "greenroom", "scripts", "wheelhouse", "greenroom.egg-info" })
        {
            string subPath = EnsureWithinBoundary(installDir, Path.Combine(installDir, dirName));
            if (Directory.Exists(subPath)) ForceDeleteDirectory(subPath);
        }

        foreach (string legacyFile in new[]
                 {
                     "AGENTS.md", "pyproject.toml", "requirements.txt", "Open Greenroom Companion.bat",
                     "USER_GUIDE.md", "AI_AGENT_README.md", "README.md", "FEATURES.md",
                     "ROADMAP.md", "CHANGELOG.md", "mother.manifest.json", "VERSION"
                 })
        {
            string fPath = EnsureWithinBoundary(installDir, Path.Combine(installDir, legacyFile));
            if (File.Exists(fPath)) ForceDeleteFile(fPath);
        }

        string appDir = EnsureWithinBoundary(installDir, Path.Combine(installDir, "app"));
        if (Directory.Exists(appDir))
        {
            foreach (string subDir in Directory.GetDirectories(appDir))
                if (!string.Equals(Path.GetFileName(subDir), ".venv", StringComparison.OrdinalIgnoreCase)) ForceDeleteDirectory(subDir);
            foreach (string file in Directory.GetFiles(appDir)) ForceDeleteFile(file);
        }

        string migrationsDir = EnsureWithinBoundary(installDir, Path.Combine(installDir, "data", "migrations"));
        if (Directory.Exists(migrationsDir))
            foreach (string oldSql in Directory.GetFiles(migrationsDir, "*.sql", SearchOption.TopDirectoryOnly)) ForceDeleteFile(oldSql);
    }

    internal static int ExtractPayloadToInstallDirectory(
        string installDir, string? externalPayloadPath, HashSet<string> managedTopFiles, Action<int, string>? report = null)
    {
        int extractedFiles = 0;
        using Stream payloadStream = OpenPayloadStream(externalPayloadPath)
            ?? throw new FileNotFoundException("Installer payload stream is unavailable.");
        using var archive = new ZipArchive(payloadStream, ZipArchiveMode.Read, leaveOpen: false);
        int total = Math.Max(1, archive.Entries.Count);
        int current = 0;

        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            current++;
            ValidateZipEntryPath(entry);
            string rel = entry.FullName.Replace('\\', '/').TrimStart('/');
            if (string.IsNullOrWhiteSpace(rel) || rel.EndsWith('/') || IsCleanRootParityExcluded(rel)) continue;

            if (IsProtectedUserRuntimeFile(rel))
            {
                string existingTarget = EnsureWithinBoundary(installDir, Path.Combine(installDir, rel.Replace('/', Path.DirectorySeparatorChar)));
                if (File.Exists(existingTarget)) continue;
            }

            string destPath = EnsureWithinBoundary(installDir, Path.Combine(installDir, rel.Replace('/', Path.DirectorySeparatorChar)));
            string? parent = Path.GetDirectoryName(destPath);
            if (!string.IsNullOrEmpty(parent))
            {
                Directory.CreateDirectory(parent);
                if (HasReparsePointOnSubpath(installDir, parent))
                    throw new InvalidDataException($"Refusing extraction through reparse point '{parent}'.");
            }
            if (File.Exists(destPath)) ForceDeleteFile(destPath);

            entry.ExtractToFile(destPath, overwrite: true);
            extractedFiles++;
            if (current == 1 || current % 25 == 0 || current == total)
                report?.Invoke(42 + (int)(16.0 * current / total), $"Extracting ({current}/{total}): {rel}");

            if (!rel.Contains('/') || rel.StartsWith("data/migrations/", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("data/schema.sql", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("data/schema_indexes.sql", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("suite.manifest.json", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("LICENSE", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("config/greenroom.example.yaml", StringComparison.OrdinalIgnoreCase) ||
                rel.Equals("config/schedule.example.yaml", StringComparison.OrdinalIgnoreCase))
            {
                managedTopFiles.Add(rel);
            }
        }
        return extractedFiles;
    }

    private static bool HasReparsePointOnSubpath(string installDir, string targetDir)
    {
        string normRoot = Path.GetFullPath(installDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        for (string? cur = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar);
             !string.IsNullOrEmpty(cur) && cur.Length > normRoot.Length;
             cur = Path.GetDirectoryName(cur))
        {
            if (Directory.Exists(cur) && (File.GetAttributes(cur) & FileAttributes.ReparsePoint) != 0) return true;
        }
        return false;
    }

    private static void MirrorAppTemplatesAndEnsureRuntimeDirs(string installDir, HashSet<string> managedTopFiles)
    {
        foreach (string schemaName in new[] { "schema.sql", "schema_indexes.sql" })
        {
            string appSchema = Path.Combine(installDir, "app", "data", schemaName);
            if (!File.Exists(appSchema)) continue;
            Directory.CreateDirectory(Path.Combine(installDir, "data"));
            string dstSchema = Path.Combine(installDir, "data", schemaName);
            if (File.Exists(dstSchema)) ForceDeleteFile(dstSchema);
            File.Copy(appSchema, dstSchema, overwrite: true);
            managedTopFiles.Add($"data/{schemaName}");
        }

        string appMigrations = Path.Combine(installDir, "app", "data", "migrations");
        string migrationsDir = Path.Combine(installDir, "data", "migrations");
        if (Directory.Exists(appMigrations))
        {
            Directory.CreateDirectory(migrationsDir);
            foreach (string sqlFile in Directory.GetFiles(appMigrations, "*.sql", SearchOption.TopDirectoryOnly))
            {
                string fname = Path.GetFileName(sqlFile);
                string dstSql = Path.Combine(migrationsDir, fname);
                if (File.Exists(dstSql)) ForceDeleteFile(dstSql);
                File.Copy(sqlFile, dstSql, overwrite: true);
                managedTopFiles.Add($"data/migrations/{fname}");
            }
        }

        string appCfgDir = Path.Combine(installDir, "app", "config");
        if (Directory.Exists(appCfgDir))
        {
            Directory.CreateDirectory(Path.Combine(installDir, "config"));
            foreach (string tplName in new[] { "greenroom.example.yaml", "schedule.example.yaml" })
            {
                string srcTpl = Path.Combine(appCfgDir, tplName);
                if (!File.Exists(srcTpl)) continue;
                string dstTpl = Path.Combine(installDir, "config", tplName);
                if (File.Exists(dstTpl)) ForceDeleteFile(dstTpl);
                File.Copy(srcTpl, dstTpl, overwrite: true);
                managedTopFiles.Add($"config/{tplName}");
            }
        }

        foreach (string runtimeSub in new[]
                 {
                     "config", "data", "data/backups", "data/dats", "data/quarantine", "data/config_snapshots",
                     "drops", "drops/agent_queue", "drops/processed", "drops/failed",
                     "drops/takeout", "drops/receipts", "drops/manual_csv", "drops/dats", "logs", "logs/crashes"
                 })
        {
            Directory.CreateDirectory(EnsureWithinBoundary(installDir, Path.Combine(installDir, runtimeSub.Replace('/', Path.DirectorySeparatorChar))));
        }

        SeedConfigIfMissing(Path.Combine(installDir, "config", "greenroom.example.yaml"), Path.Combine(installDir, "config", "greenroom.yaml"));
        SeedConfigIfMissing(Path.Combine(installDir, "config", "schedule.example.yaml"), Path.Combine(installDir, "config", "schedule.yaml"));
    }
}
