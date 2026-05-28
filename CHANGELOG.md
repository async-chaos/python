# Changelog

All notable changes to asynchaos will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-27
### Added
- `@inject_latency` decorator
- `@drop_connections` decorator
- `@timeout` decorator
- `@chaos` combined decorator
- `chaos_zone` async context manager with `contextvars` propagation
- `chaos_patch` monkey-patching helper for async client classes
- `ProbabilityCondition` and `RateCondition` for flexible trigger logic
- Global `enable()` / `disable()` / `configure()` API
