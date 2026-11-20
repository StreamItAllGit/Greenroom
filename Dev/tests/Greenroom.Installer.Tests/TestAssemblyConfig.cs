// @module Greenroom.Installer.Tests.AssemblyConfig
// @purpose Disables xUnit cross-class parallel execution so environment variable and sandbox registry tests stay deterministic.
// @inputs Xunit test runner assembly configuration
// @outputs Serialized xUnit test collection behavior (@exports CollectionBehaviorAttribute)

using Xunit;

[assembly: CollectionBehavior(DisableTestParallelization = true)]
