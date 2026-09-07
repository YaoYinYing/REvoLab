# ADR-0004: Driver Protocol And Registry

## Context
Providers need independent capabilities without provider-specific branches in Core.

## Decision
Use Python `Protocol`, an application-scoped `DriverRegistry`, and `importlib.metadata` entry points.

## Consequences
Drivers can be discovered and validated without a custom framework; lifecycle remains explicit.

## Rejected alternatives
Provider branches in Core, a custom plugin framework, or pluggy before a real 1:N hook exists.
