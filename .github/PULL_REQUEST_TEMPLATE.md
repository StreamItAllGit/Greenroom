## Summary

<!-- Briefly describe what this pull request changes and why. -->

## Type of Change

- [ ] Bug fix (non-breaking change addressing an issue)
- [ ] New feature or domain enhancement
- [ ] UI / Companion PWA polish
- [ ] Packaging, installer, CI, or documentation update

## Verification Checklist

- [ ] `APP_ISOLATED_TEST=1 pytest Dev/tests -q` passes with zero failures
- [ ] `dotnet test Dev/tests/Greenroom.Installer.Tests/Greenroom.Installer.Tests.csproj -c Release -p:TreatWarningsAsErrors=true` passes with zero warnings
- [ ] Tests stay strictly isolated (`APP_ISOLATED_TEST=1`, temp directories only) with no host registry or production path mutations
- [ ] Zero workstation paths, secrets, tokens, personal handles, or copyrighted ROM/DAT assets included
- [ ] Updated `Dev/docs/` and `CHANGELOG.md` where user-facing behavior or interfaces changed
